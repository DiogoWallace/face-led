from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities.base import ProcessAggregate
from app.domain.value_objects.status import ProcessStatus, RejectionReason


@dataclass(kw_only=True)
class Verification(ProcessAggregate):
    """Tentativa posterior de validação 1:1 contra o FaceRegistration ativo."""

    idempotency_key: str
    capture_object_key: str | None
    expires_at: datetime
    face_registration_id: UUID | None = None
    policy_version: str | None = None
    # Mantido para auditoria/calibração; não é exposto na API.
    similarity: float | None = None
    # Liveness ativo (ADR-010): sessão de desafio consumida por esta validação.
    liveness_challenge_id: UUID | None = None

    @classmethod
    def create(
        cls,
        *,
        id: UUID,
        tenant_id: UUID,
        subject_id: UUID,
        idempotency_key: str,
        capture_object_key: str,
        expires_at: datetime,
        now: datetime,
    ) -> "Verification":
        return cls(
            id=id,
            tenant_id=tenant_id,
            subject_id=subject_id,
            status=ProcessStatus.CREATED,
            reason=None,
            idempotency_key=idempotency_key,
            capture_object_key=capture_object_key,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def record_match(
        self,
        *,
        matched: bool,
        face_registration_id: UUID,
        similarity: float,
        policy_version: str,
        now: datetime,
    ) -> None:
        self.face_registration_id = face_registration_id
        self.similarity = similarity
        self.policy_version = policy_version
        if matched:
            self._transition(ProcessStatus.APPROVED, now)
            self.reason = None
        else:
            self.reject(RejectionReason.FACE_MISMATCH, now)
