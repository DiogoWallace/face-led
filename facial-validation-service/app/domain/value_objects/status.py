"""Estados explícitos dos processos de cadastro e validação.

REJECTED: o processamento foi concluído e a pessoa NÃO passou na validação.
ERROR:    o sistema NÃO conseguiu concluir o processamento (resultado inconclusivo).
"""

from enum import StrEnum

from app.domain.exceptions import InvalidStateTransition


class ProcessStatus(StrEnum):
    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = frozenset(
    {ProcessStatus.APPROVED, ProcessStatus.REJECTED, ProcessStatus.ERROR, ProcessStatus.EXPIRED}
)

_ALLOWED_TRANSITIONS: dict[ProcessStatus, frozenset[ProcessStatus]] = {
    ProcessStatus.CREATED: frozenset(
        {ProcessStatus.PROCESSING, ProcessStatus.ERROR, ProcessStatus.EXPIRED}
    ),
    ProcessStatus.PROCESSING: frozenset(
        {ProcessStatus.APPROVED, ProcessStatus.REJECTED, ProcessStatus.ERROR, ProcessStatus.EXPIRED}
    ),
    ProcessStatus.APPROVED: frozenset(),
    ProcessStatus.REJECTED: frozenset(),
    ProcessStatus.ERROR: frozenset(),
    ProcessStatus.EXPIRED: frozenset(),
}


def ensure_transition(current: ProcessStatus, target: ProcessStatus) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidStateTransition(f"transição inválida: {current} -> {target}")


class RejectionReason(StrEnum):
    """Motivos de REJECTED: processo concluído, pessoa não aprovada."""

    NO_FACE = "NO_FACE"
    MULTIPLE_FACES = "MULTIPLE_FACES"
    LOW_QUALITY = "LOW_QUALITY"
    LIVENESS_FAILED = "LIVENESS_FAILED"
    FACE_MISMATCH = "FACE_MISMATCH"


class ErrorReason(StrEnum):
    """Motivos de ERROR: o sistema não conseguiu chegar a uma decisão."""

    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    LIVENESS_NOT_CONFIGURED = "LIVENESS_NOT_CONFIGURED"
    LIVENESS_INCONCLUSIVE = "LIVENESS_INCONCLUSIVE"
    QUALITY_MEASUREMENT_UNAVAILABLE = "QUALITY_MEASUREMENT_UNAVAILABLE"
    POLICY_NOT_CONFIGURED = "POLICY_NOT_CONFIGURED"
    MODEL_MISMATCH = "MODEL_MISMATCH"
    REFERENCE_NOT_FOUND = "REFERENCE_NOT_FOUND"
    CAPTURE_UNAVAILABLE = "CAPTURE_UNAVAILABLE"
    EXPIRED = "EXPIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class Decision(StrEnum):
    """Decisão padronizada devolvida aos sistemas consumidores."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"

    @classmethod
    def from_status(cls, status: ProcessStatus) -> "Decision | None":
        if status is ProcessStatus.APPROVED:
            return cls.APPROVED
        if status is ProcessStatus.REJECTED:
            return cls.REJECTED
        if status in (ProcessStatus.ERROR, ProcessStatus.EXPIRED):
            return cls.ERROR
        return None
