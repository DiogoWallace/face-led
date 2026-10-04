from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.value_objects import CaptureData, Decision, ProcessStatus


@dataclass(frozen=True, slots=True)
class CapturePolicy:
    max_bytes: int
    allowed_content_types: frozenset[str] = frozenset({"image/jpeg", "image/png"})


@dataclass(frozen=True, slots=True)
class LivenessIntakePolicy:
    """Como a API recebe a evidência do liveness ativo (ADR-010)."""

    required: bool = False
    min_frames: int = 8
    max_frames: int = 120
    frame_max_bytes: int = 300_000


@dataclass(frozen=True, slots=True)
class LivenessSubmission:
    """O que o front manda junto da selfie: a sessão de desafio e os quadros capturados."""

    session_id: UUID
    frames: tuple[CaptureData, ...]


@dataclass(frozen=True, slots=True)
class LivenessSessionView:
    session_id: UUID
    external_subject_id: str
    purpose: str
    steps: tuple[str, ...]
    expires_at: datetime
    min_frames: int
    max_frames: int
    frame_max_bytes: int


@dataclass(frozen=True, slots=True)
class RegisterFaceCommand:
    tenant_id: UUID
    external_subject_id: str
    capture: CaptureData
    # Recadastro (PUT): substitui a referência ativa quando o novo envio for aprovado.
    replace: bool = False
    liveness: LivenessSubmission | None = None


@dataclass(frozen=True, slots=True)
class FaceRegistrationAccepted:
    registration_id: UUID
    external_subject_id: str
    status: ProcessStatus


@dataclass(frozen=True, slots=True)
class CreateVerificationCommand:
    tenant_id: UUID
    external_subject_id: str
    idempotency_key: str
    capture: CaptureData
    liveness: LivenessSubmission | None = None


@dataclass(frozen=True, slots=True)
class VerificationView:
    verification_id: UUID
    external_subject_id: str
    status: ProcessStatus
    decision: Decision | None
    reason: str | None
    policy_version: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    replayed: bool = False
    quality_issues: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class FaceRegistrationView:
    registration_id: UUID
    external_subject_id: str
    status: ProcessStatus
    decision: Decision | None
    reason: str | None
    quality_issues: tuple[str, ...] | None
    created_at: datetime
    updated_at: datetime
    replaces_registration_id: UUID | None = None
    superseded_at: datetime | None = None
    superseded_by_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SubjectFaceView:
    external_subject_id: str
    active_registration_id: UUID | None
    latest_registration: FaceRegistrationView | None

    @property
    def enrolled(self) -> bool:
        return self.active_registration_id is not None
