from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.exceptions import InvalidStateTransition
from app.domain.value_objects.status import (
    Decision,
    ErrorReason,
    ProcessStatus,
    RejectionReason,
    ensure_transition,
)


@dataclass(kw_only=True)
class ProcessAggregate:
    """Base de agregados com ciclo de vida CREATED -> PROCESSING -> terminal."""

    id: UUID
    tenant_id: UUID
    subject_id: UUID
    status: ProcessStatus
    reason: str | None
    capture_object_key: str | None
    created_at: datetime
    updated_at: datetime
    # Códigos do QualityGate (BLURRY, TOO_DARK...). None = qualidade não avaliada
    # (ex.: falha antes do gate); () = avaliada e aprovada. Nunca medidas numéricas.
    quality_issues: tuple[str, ...] | None = None

    @property
    def decision(self) -> Decision | None:
        return Decision.from_status(self.status)

    def record_quality(self, issues: tuple[str, ...] | None) -> None:
        self.quality_issues = None if issues is None else tuple(issues)

    def _transition(self, target: ProcessStatus, now: datetime) -> None:
        ensure_transition(self.status, target)
        self.status = target
        self.updated_at = now

    def start_processing(self, now: datetime) -> None:
        self._transition(ProcessStatus.PROCESSING, now)

    def reject(self, reason: RejectionReason, now: datetime) -> None:
        self._transition(ProcessStatus.REJECTED, now)
        self.reason = reason.value

    def fail(self, reason: ErrorReason, now: datetime) -> None:
        self._transition(ProcessStatus.ERROR, now)
        self.reason = reason.value

    def expire(self, now: datetime) -> None:
        self._transition(ProcessStatus.EXPIRED, now)
        self.reason = ErrorReason.EXPIRED.value

    def discard_capture(self, now: datetime) -> str | None:
        """Esquece a chave da captura depois do processamento. Devolve a chave antiga.

        Só em estado terminal: antes disso o worker ainda precisa da imagem.
        """
        if not self.status.is_terminal:
            raise InvalidStateTransition("a captura só pode ser descartada após o processamento")
        key = self.capture_object_key
        if key is not None:
            self.capture_object_key = None
            self.updated_at = now
        return key
