import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from app.application.dto import CapturePolicy
from app.application.ports import (
    CaptureStorage,
    CaptureStorageError,
    Clock,
    IdGenerator,
    UnitOfWorkFactory,
)
from app.domain.entities import VerificationEvent
from app.domain.entities.base import ProcessAggregate
from app.domain.exceptions import InvalidCapture
from app.domain.repositories import UnitOfWork
from app.domain.value_objects import CaptureData, CaptureRetention

logger = logging.getLogger(__name__)

_SIGNATURES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}


def validate_capture(capture: CaptureData, policy: CapturePolicy) -> None:
    if capture.content_type not in policy.allowed_content_types:
        raise InvalidCapture("tipo de arquivo não suportado")
    if not capture.content:
        raise InvalidCapture("arquivo vazio")
    if len(capture.content) > policy.max_bytes:
        raise InvalidCapture("arquivo excede o tamanho máximo")
    signatures = _SIGNATURES.get(capture.content_type, ())
    if signatures and not capture.content.startswith(signatures):
        raise InvalidCapture("conteúdo não corresponde ao tipo informado")


def capture_key(tenant_id: UUID, subject_id: UUID, kind: str, object_id: UUID) -> str:
    # Somente UUIDs internos: o external_id do consumidor pode ser dado pessoal (ex.: CPF).
    return f"tenants/{tenant_id}/subjects/{subject_id}/{kind}/{object_id}"


async def record_event(
    uow: UnitOfWork,
    new_id: IdGenerator,
    *,
    tenant_id: UUID,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: str,
    occurred_at: Any,
    data: dict[str, Any] | None = None,
) -> None:
    await uow.events.add(
        VerificationEvent(
            id=new_id(),
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            data=data or {},
            occurred_at=occurred_at,
        )
    )


async def release_capture(
    *,
    retention: CaptureRetention,
    storage: CaptureStorage,
    uow_factory: UnitOfWorkFactory,
    new_id: IdGenerator,
    clock: Clock,
    aggregate: ProcessAggregate,
    aggregate_type: str,
    save: Callable[[UnitOfWork, Any], Awaitable[None]],
    extra_keys: tuple[str, ...] = (),
) -> None:
    """Aplica a retenção da captura depois que o resultado já foi gravado.

    Ordem: apaga do storage e SÓ ENTÃO remove a chave do registro. Se o storage
    falhar, a chave continua apontando para a captura, que segue existindo —
    nunca o contrário (registro dizendo "apagada" com a imagem ainda guardada).
    Falha aqui não muda o resultado do processamento.
    """
    if retention is CaptureRetention.KEEP or aggregate.capture_object_key is None:
        return
    try:
        # Quadros do liveness (ADR-010) também são biometria: seguem a mesma retenção.
        for key in (*extra_keys, aggregate.capture_object_key):
            await storage.delete(key)
    except CaptureStorageError:
        logger.warning(
            "capture_delete_failed",
            extra={"aggregate_type": aggregate_type, "aggregate_id": str(aggregate.id)},
        )
        return
    now = clock.now()
    aggregate.discard_capture(now)
    async with uow_factory() as uow:
        await save(uow, aggregate)
        await record_event(
            uow,
            new_id,
            tenant_id=aggregate.tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate.id,
            event_type="CAPTURE_DELETED",
            occurred_at=now,
            data={"retention": retention.value, "liveness_frames": len(extra_keys)},
        )
        await uow.commit()
