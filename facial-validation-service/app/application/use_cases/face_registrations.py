"""Consulta do cadastro facial (Fase 6).

Escopo por tenant: cadastro ou subject de outro tenant é 404, como na validação.
Nunca devolve template, modelo, medidas numéricas de qualidade nem score.
"""

from uuid import UUID

from app.application.dto import FaceRegistrationView, SubjectFaceView
from app.application.ports import UnitOfWorkFactory
from app.domain.entities import FaceRegistration, Subject
from app.domain.exceptions import FaceRegistrationNotFound, SubjectNotFound


def to_view(registration: FaceRegistration, subject: Subject) -> FaceRegistrationView:
    return FaceRegistrationView(
        registration_id=registration.id,
        external_subject_id=subject.external_id,
        status=registration.status,
        decision=registration.decision,
        reason=registration.reason,
        quality_issues=registration.quality_issues,
        created_at=registration.created_at,
        updated_at=registration.updated_at,
        replaces_registration_id=registration.replaces_registration_id,
        superseded_at=registration.superseded_at,
        superseded_by_id=registration.superseded_by_id,
    )


class GetFaceRegistration:
    """Resultado de um envio específico (o registration_id devolvido pelo POST)."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, tenant_id: UUID, registration_id: UUID) -> FaceRegistrationView:
        async with self._uow_factory() as uow:
            registration = await uow.face_registrations.get_for_tenant(tenant_id, registration_id)
            if registration is None:
                raise FaceRegistrationNotFound("cadastro não encontrado")
            subject = await uow.subjects.get(tenant_id, registration.subject_id)
            if subject is None:
                raise FaceRegistrationNotFound("cadastro não encontrado")
            return to_view(registration, subject)


class GetSubjectFace:
    """Estado do cadastro do subject: há referência ativa? e o envio mais recente."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, tenant_id: UUID, external_subject_id: str) -> SubjectFaceView:
        async with self._uow_factory() as uow:
            subject = await uow.subjects.get_by_external_id(tenant_id, external_subject_id)
            if subject is None:
                raise SubjectNotFound("subject não encontrado")
            active = await uow.face_registrations.get_active_for_subject(tenant_id, subject.id)
            latest = await uow.face_registrations.get_latest_for_subject(tenant_id, subject.id)
            return SubjectFaceView(
                external_subject_id=subject.external_id,
                active_registration_id=active.id if active else None,
                latest_registration=to_view(latest, subject) if latest else None,
            )
