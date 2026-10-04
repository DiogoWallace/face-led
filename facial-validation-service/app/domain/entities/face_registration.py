from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.domain.entities.base import ProcessAggregate
from app.domain.exceptions import InvalidStateTransition
from app.domain.value_objects.status import ProcessStatus


@dataclass(kw_only=True)
class FaceRegistration(ProcessAggregate):
    """Referência biométrica inicial de um Subject (cadastro facial).

    `template` guarda o embedding já cifrado (bytes opacos); nunca o vetor em claro.
    """

    capture_object_key: str | None
    model_name: str | None = None
    model_version: str | None = None
    template: bytes | None = field(default=None, repr=False)
    # Liveness ativo (ADR-010): sessão de desafio consumida por este envio.
    liveness_challenge_id: UUID | None = None
    # Recadastro (ADR-008): este envio substitui a referência ativa quando aprovado.
    replaces_registration_id: UUID | None = None
    # Preenchidos quando ESTA referência é substituída por outra aprovada.
    superseded_at: datetime | None = None
    superseded_by_id: UUID | None = None

    @classmethod
    def create(
        cls,
        *,
        id: UUID,
        tenant_id: UUID,
        subject_id: UUID,
        capture_object_key: str,
        now: datetime,
        replaces_registration_id: UUID | None = None,
    ) -> "FaceRegistration":
        return cls(
            id=id,
            tenant_id=tenant_id,
            subject_id=subject_id,
            status=ProcessStatus.CREATED,
            reason=None,
            capture_object_key=capture_object_key,
            replaces_registration_id=replaces_registration_id,
            created_at=now,
            updated_at=now,
        )

    @property
    def is_replacement(self) -> bool:
        return self.replaces_registration_id is not None

    def approve(
        self, *, template: bytes, model_name: str, model_version: str, now: datetime
    ) -> None:
        self._transition(ProcessStatus.APPROVED, now)
        self.template = template
        self.model_name = model_name
        self.model_version = model_version
        self.reason = None

    @property
    def is_active_reference(self) -> bool:
        return (
            self.status is ProcessStatus.APPROVED
            and self.superseded_at is None
            and self.template is not None
        )

    def supersede(self, *, by: UUID, now: datetime) -> None:
        """Aposenta esta referência: outra foi aprovada no lugar (ADR-008).

        O template é APAGADO (minimização, LGPD). O registro continua APPROVED
        como histórico, sem biometria.
        """
        if not self.is_active_reference:
            raise InvalidStateTransition("só a referência ativa pode ser substituída")
        if by == self.id:
            raise InvalidStateTransition("uma referência não substitui a si mesma")
        self.superseded_at = now
        self.superseded_by_id = by
        self.template = None
        self.updated_at = now
