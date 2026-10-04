"""Entrega de webhook (outbox transacional). ADR-009.

Criada na MESMA transação que grava o resultado do cadastro ou da validação,
então nenhum resultado fica sem aviso se a fila cair nesse instante. Guarda só
o tipo do evento e o id do recurso: o corpo é montado na hora do envio, igual ao
GET (ADR-007), e nenhum dado pessoal fica parado aqui.

Entrega "pelo menos uma vez": o consumidor deduplica pelo `id` (event_id).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from app.domain.exceptions import InvalidStateTransition

# Espera depois de cada tentativa falha: 9 tentativas em ~23,8 h (decisão do usuário: ~24 h).
RETRY_DELAYS: tuple[timedelta, ...] = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=1),
    timedelta(hours=2),
    timedelta(hours=4),
    timedelta(hours=8),
    timedelta(hours=8),
)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1


class WebhookEventType(StrEnum):
    FACE_REGISTRATION_COMPLETED = "face_registration.completed"
    VERIFICATION_COMPLETED = "verification.completed"


class WebhookDeliveryStatus(StrEnum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"  # desistiu: tentativas esgotadas ou webhook desligado


@dataclass(kw_only=True)
class WebhookDelivery:
    id: UUID  # vai no corpo e no header como event_id
    tenant_id: UUID
    event_type: WebhookEventType
    resource_id: UUID
    status: WebhookDeliveryStatus
    attempts: int
    next_attempt_at: datetime
    created_at: datetime
    updated_at: datetime
    last_attempt_at: datetime | None = None
    last_status_code: int | None = None
    last_error: str | None = None  # código curto; nunca o corpo da resposta
    delivered_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        id: UUID,
        tenant_id: UUID,
        event_type: WebhookEventType,
        resource_id: UUID,
        now: datetime,
    ) -> "WebhookDelivery":
        return cls(
            id=id,
            tenant_id=tenant_id,
            event_type=event_type,
            resource_id=resource_id,
            status=WebhookDeliveryStatus.PENDING,
            attempts=0,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )

    def _require_pending(self) -> None:
        if self.status is not WebhookDeliveryStatus.PENDING:
            raise InvalidStateTransition(f"entrega já encerrada: {self.status}")

    def claim(self, now: datetime, lease: timedelta) -> None:
        """Reserva a entrega para uma tentativa.

        Empurra `next_attempt_at` para depois do prazo da reserva: se o worker
        morrer no meio do envio, a varredura tenta de novo quando o prazo vencer.
        """
        self._require_pending()
        self.attempts += 1
        self.last_attempt_at = now
        self.next_attempt_at = now + lease
        self.updated_at = now

    def succeed(self, now: datetime, status_code: int) -> None:
        self._require_pending()
        self.status = WebhookDeliveryStatus.DELIVERED
        self.last_status_code, self.last_error = status_code, None
        self.delivered_at = self.updated_at = now

    def fail(self, now: datetime, *, error: str, status_code: int | None = None) -> None:
        """Tentativa falhou: agenda a próxima pelo backoff ou desiste."""
        self._require_pending()
        self.last_status_code, self.last_error = status_code, error[:64]
        self.updated_at = now
        if self.attempts >= MAX_ATTEMPTS:
            self.status = WebhookDeliveryStatus.FAILED
        else:
            self.next_attempt_at = now + RETRY_DELAYS[max(self.attempts, 1) - 1]

    def abandon(self, now: datetime, error: str) -> None:
        """Encerra sem novas tentativas (ex.: o tenant desligou o webhook)."""
        self._require_pending()
        self.status = WebhookDeliveryStatus.FAILED
        self.last_error = error[:64]
        self.updated_at = now
