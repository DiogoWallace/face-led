from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.value_objects.biometric import LivenessResult, LivenessVerdict


class LivenessPurpose(StrEnum):
    REGISTRATION = "REGISTRATION"
    VERIFICATION = "VERIFICATION"


@dataclass(kw_only=True)
class LivenessSession:
    id: UUID
    tenant_id: UUID
    subject_id: UUID
    purpose: LivenessPurpose
    reference_id: UUID
    provider: str
    verdict: LivenessVerdict | None
    score: float | None
    created_at: datetime
    completed_at: datetime | None
    detail: str | None = None

    @classmethod
    def from_result(
        cls,
        *,
        id: UUID,
        tenant_id: UUID,
        subject_id: UUID,
        purpose: LivenessPurpose,
        reference_id: UUID,
        provider: str,
        result: LivenessResult,
        now: datetime,
    ) -> "LivenessSession":
        return cls(
            id=id,
            tenant_id=tenant_id,
            subject_id=subject_id,
            purpose=purpose,
            reference_id=reference_id,
            provider=provider,
            verdict=result.verdict,
            score=result.score,
            detail=result.detail,
            created_at=now,
            completed_at=now,
        )
