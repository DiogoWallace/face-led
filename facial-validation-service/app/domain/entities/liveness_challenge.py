"""Sessão de desafio de liveness (ADR-010): criada ANTES da captura.

Uso único e com prazo. Amarrada ao tenant, ao subject (id do consumidor) e à
finalidade: quem pediu o desafio para cadastrar não o usa para validar, nem
para outro subject. Ao ser consumida, guarda quem a usou e as chaves dos
quadros (para a retenção apagá-los junto com a captura).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from app.domain.entities.liveness_session import LivenessPurpose
from app.domain.exceptions import LivenessSessionInvalid


class LivenessChallengeStatus(StrEnum):
    CREATED = "CREATED"
    USED = "USED"


@dataclass(kw_only=True)
class LivenessChallenge:
    id: UUID
    tenant_id: UUID
    external_subject_id: str
    purpose: LivenessPurpose
    steps: tuple[str, ...]
    status: LivenessChallengeStatus
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    used_at: datetime | None = None
    used_by_id: UUID | None = None
    frame_keys: tuple[str, ...] = field(default=())

    @classmethod
    def create(
        cls,
        *,
        id: UUID,
        tenant_id: UUID,
        external_subject_id: str,
        purpose: LivenessPurpose,
        steps: tuple[str, ...],
        ttl: timedelta,
        now: datetime,
    ) -> "LivenessChallenge":
        return cls(
            id=id,
            tenant_id=tenant_id,
            external_subject_id=external_subject_id,
            purpose=purpose,
            steps=steps,
            status=LivenessChallengeStatus.CREATED,
            expires_at=now + ttl,
            created_at=now,
            updated_at=now,
        )

    def consume(
        self,
        *,
        external_subject_id: str,
        purpose: LivenessPurpose,
        by_id: UUID,
        frame_keys: tuple[str, ...],
        now: datetime,
    ) -> None:
        if self.status is LivenessChallengeStatus.USED:
            raise LivenessSessionInvalid("sessão de liveness já utilizada")
        if now >= self.expires_at:
            raise LivenessSessionInvalid("sessão de liveness expirada")
        if self.external_subject_id != external_subject_id or self.purpose is not purpose:
            raise LivenessSessionInvalid("sessão de liveness de outro subject ou finalidade")
        self.status = LivenessChallengeStatus.USED
        self.used_at = self.updated_at = now
        self.used_by_id = by_id
        self.frame_keys = frame_keys
