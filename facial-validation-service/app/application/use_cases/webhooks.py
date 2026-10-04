"""Webhook de resultado (ADR-009): configuração, outbox e entrega.

- A URL e o segredo são por tenant e só o operador os configura (CLI). Quem
  chama a API não escolhe para onde o serviço faz requisições (evita SSRF).
- `schedule_result_webhook` roda DENTRO da transação que grava o resultado
  (outbox): se o enqueue falhar depois do commit, a varredura entrega.
- `DeliverWebhooks` monta o corpo NA HORA DO ENVIO com o mesmo serializador do
  GET, assina (HMAC-SHA256 com timestamp) e aplica o backoff da entidade.
"""

import json
import logging
import secrets
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import UUID

from app.application.ports import (
    Clock,
    IdGenerator,
    ResultRenderer,
    TaskQueue,
    TaskQueueError,
    TemplateCipher,
    UnitOfWorkFactory,
    WebhookSender,
    WebhookTransportError,
)
from app.application.use_cases import face_registrations, verifications
from app.domain.entities import Tenant, WebhookDelivery, WebhookEventType
from app.domain.repositories import UnitOfWork

logger = logging.getLogger(__name__)

SECRET_PREFIX = "whsec_"  # noqa: S105 - prefixo identificador, não um segredo
MAX_URL_LENGTH = 2048


class InvalidWebhookUrl(ValueError):
    pass


def webhook_secret_aad(tenant_id: UUID) -> bytes:
    """Amarra o segredo cifrado ao tenant: não serve se copiado para outra linha."""
    return f"webhook-secret:{tenant_id}".encode()


def validate_webhook_url(url: str, *, allow_http: bool) -> str:
    url = url.strip()
    parts = urlsplit(url)
    allowed = {"https", "http"} if allow_http else {"https"}
    if parts.scheme not in allowed:
        raise InvalidWebhookUrl(
            "a URL do webhook deve usar https" + (" ou http" if allow_http else "")
        )
    if not parts.hostname:
        raise InvalidWebhookUrl("a URL do webhook precisa de host")
    if parts.username or parts.password:
        raise InvalidWebhookUrl("credenciais na URL não são aceitas; use a assinatura")
    if parts.fragment:
        raise InvalidWebhookUrl("a URL do webhook não pode ter fragmento (#)")
    if len(url) > MAX_URL_LENGTH:
        raise InvalidWebhookUrl("URL do webhook longa demais")
    return url


class ConfigureTenantWebhook:
    """Liga ou atualiza o webhook de um tenant. Devolve o segredo SÓ quando gera um novo."""

    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, cipher: TemplateCipher, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._cipher = cipher
        self._clock = clock

    async def execute(
        self, *, slug: str, url: str, rotate_secret: bool, allow_http: bool
    ) -> str | None:
        url = validate_webhook_url(url, allow_http=allow_http)
        async with self._uow_factory() as uow:
            tenant = await _tenant_by_slug(uow, slug)
            new_secret = None
            encrypted = tenant.webhook_secret
            if encrypted is None or rotate_secret:
                new_secret = SECRET_PREFIX + secrets.token_urlsafe(32)
                encrypted = self._cipher.encrypt(
                    new_secret.encode(), associated_data=webhook_secret_aad(tenant.id)
                )
            tenant.configure_webhook(url=url, encrypted_secret=encrypted, now=self._clock.now())
            await uow.tenants.save(tenant)
            await uow.commit()
        return new_secret


class DisableTenantWebhook:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, *, slug: str) -> None:
        async with self._uow_factory() as uow:
            tenant = await _tenant_by_slug(uow, slug)
            tenant.disable_webhook(self._clock.now())
            await uow.tenants.save(tenant)
            await uow.commit()


async def _tenant_by_slug(uow: UnitOfWork, slug: str) -> Tenant:
    tenant = await uow.tenants.get_by_slug(slug)
    if tenant is None:
        raise LookupError(f"tenant não encontrado: {slug}")
    return tenant


async def schedule_result_webhook(
    uow: UnitOfWork,
    *,
    tenant_id: UUID,
    event_type: WebhookEventType,
    resource_id: UUID,
    new_id: IdGenerator,
    clock: Clock,
) -> UUID | None:
    """Grava a entrega no outbox, na transação do resultado. None se o tenant não usa webhook."""
    tenant = await uow.tenants.get(tenant_id)
    if tenant is None or not tenant.webhook_enabled:
        return None
    delivery = WebhookDelivery.create(
        id=new_id(),
        tenant_id=tenant_id,
        event_type=event_type,
        resource_id=resource_id,
        now=clock.now(),
    )
    await uow.webhook_deliveries.add(delivery)
    return delivery.id


async def trigger_delivery(queue: TaskQueue, delivery_id: UUID | None) -> None:
    """Atalho de latência, depois do commit. Se a fila falhar, a varredura entrega."""
    if delivery_id is None:
        return
    try:
        await queue.enqueue_webhook_delivery(delivery_id)
    except TaskQueueError:
        logger.warning("webhook_enqueue_failed", extra={"delivery_id": str(delivery_id)})


class DeliverWebhooks:
    """Varre e entrega o que está vencido. Chamado pelo cron e pelo atalho pós-commit."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        sender: WebhookSender,
        cipher: TemplateCipher,
        renderer: ResultRenderer,
        clock: Clock,
        batch_size: int = 20,
        lease: timedelta = timedelta(minutes=2),
    ) -> None:
        self._uow_factory = uow_factory
        self._sender = sender
        self._cipher = cipher
        self._renderer = renderer
        self._clock = clock
        self._batch_size = batch_size
        self._lease = lease

    async def execute(self) -> int:
        async with self._uow_factory() as uow:
            due = await uow.webhook_deliveries.claim_due(
                self._clock.now(), self._lease, self._batch_size
            )
            await uow.commit()
        for delivery in due:
            await self._deliver(delivery)
        return len(due)

    async def _deliver(self, delivery: WebhookDelivery) -> None:
        async with self._uow_factory() as uow:
            tenant = await uow.tenants.get(delivery.tenant_id)
            data = await self._render(uow, delivery) if tenant else None

        if tenant is None or not tenant.webhook_enabled:
            await self._finish(delivery, abandon="WEBHOOK_DISABLED")
            return
        if data is None:
            await self._finish(delivery, abandon="RESOURCE_NOT_FOUND")
            return

        body = json.dumps(
            {
                "id": str(delivery.id),
                "type": delivery.event_type.value,
                "created_at": delivery.created_at.isoformat(),
                "data": data,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        secret = self._cipher.decrypt(
            tenant.webhook_secret or b"", associated_data=webhook_secret_aad(tenant.id)
        ).decode()
        try:
            status_code = await self._sender.deliver(
                url=tenant.webhook_url or "",
                secret=secret,
                event_id=delivery.id,
                event_type=delivery.event_type.value,
                body=body,
            )
        except WebhookTransportError as error:
            await self._finish(delivery, error=error.code)
            return
        if 200 <= status_code < 300:
            await self._finish(delivery, delivered=status_code)
        else:
            await self._finish(delivery, error=f"HTTP_{status_code}", status_code=status_code)

    async def _render(self, uow: UnitOfWork, delivery: WebhookDelivery) -> dict | None:
        if delivery.event_type is WebhookEventType.FACE_REGISTRATION_COMPLETED:
            registration = await uow.face_registrations.get_for_tenant(
                delivery.tenant_id, delivery.resource_id
            )
            subject = registration and await uow.subjects.get(
                delivery.tenant_id, registration.subject_id
            )
            if not registration or not subject:
                return None
            return self._renderer.face_registration(
                face_registrations.to_view(registration, subject)
            )
        verification = await uow.verifications.get_for_tenant(
            delivery.tenant_id, delivery.resource_id
        )
        subject = verification and await uow.subjects.get(
            delivery.tenant_id, verification.subject_id
        )
        if not verification or not subject:
            return None
        return self._renderer.verification(verifications.to_view(verification, subject))

    async def _finish(
        self,
        delivery: WebhookDelivery,
        *,
        delivered: int | None = None,
        error: str | None = None,
        status_code: int | None = None,
        abandon: str | None = None,
    ) -> None:
        now = self._clock.now()
        if delivered is not None:
            delivery.succeed(now, delivered)
        elif abandon is not None:
            delivery.abandon(now, abandon)
        else:
            delivery.fail(now, error=error or "UNKNOWN", status_code=status_code)
        async with self._uow_factory() as uow:
            await uow.webhook_deliveries.save(delivery)
            await uow.commit()
        logger.info(
            "webhook_delivery",
            extra={
                "delivery_id": str(delivery.id),
                "tenant_id": str(delivery.tenant_id),
                "event_type": delivery.event_type.value,
                "status": delivery.status.value,
                "attempt": delivery.attempts,
                "status_code": delivery.last_status_code,
                "error": delivery.last_error,
            },
        )
