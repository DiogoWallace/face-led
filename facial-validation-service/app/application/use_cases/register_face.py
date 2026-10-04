from uuid import UUID

from app.application.dto import (
    CapturePolicy,
    FaceRegistrationAccepted,
    LivenessIntakePolicy,
    RegisterFaceCommand,
)
from app.application.ports import (
    CaptureStorage,
    Clock,
    IdGenerator,
    TaskQueue,
    UnitOfWorkFactory,
)
from app.application.use_cases._common import capture_key, record_event, validate_capture
from app.application.use_cases.liveness import (
    NO_LIVENESS_INTAKE,
    check_liveness_submission,
    consume_liveness,
)
from app.domain.entities import FaceRegistration, LivenessPurpose, Subject
from app.domain.exceptions import SubjectAlreadyEnrolled, SubjectNotEnrolled, SubjectNotFound


class RegisterFace:
    """Recebe a primeira captura e agenda o processamento do cadastro facial."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        storage: CaptureStorage,
        queue: TaskQueue,
        clock: Clock,
        new_id: IdGenerator,
        capture_policy: CapturePolicy,
        liveness_intake: LivenessIntakePolicy = NO_LIVENESS_INTAKE,
    ) -> None:
        self._liveness_intake = liveness_intake
        self._uow_factory = uow_factory
        self._storage = storage
        self._queue = queue
        self._clock = clock
        self._new_id = new_id
        self._capture_policy = capture_policy

    async def execute(self, command: RegisterFaceCommand) -> FaceRegistrationAccepted:
        validate_capture(command.capture, self._capture_policy)
        check_liveness_submission(command.liveness, self._liveness_intake)
        now = self._clock.now()

        async with self._uow_factory() as uow:
            subject = await uow.subjects.get_by_external_id(
                command.tenant_id, command.external_subject_id
            )
            replaces: UUID | None = None
            if subject is None:
                if command.replace:
                    raise SubjectNotFound("subject não encontrado")
                subject = Subject(
                    id=self._new_id(),
                    tenant_id=command.tenant_id,
                    external_id=command.external_subject_id,
                    created_at=now,
                    updated_at=now,
                )
                await uow.subjects.add(subject)
            else:
                active = await uow.face_registrations.get_active_for_subject(
                    command.tenant_id, subject.id
                )
                if command.replace:
                    # Recadastro (ADR-008): o consumidor decide quando é legítimo; a
                    # referência atual só sai quando o novo envio for aprovado.
                    if active is None:
                        raise SubjectNotEnrolled("subject sem cadastro facial ativo")
                    replaces = active.id
                elif active is not None:
                    raise SubjectAlreadyEnrolled(
                        "subject já possui cadastro facial ativo (recadastro: PUT)"
                    )

            registration_id = self._new_id()
            key = capture_key(command.tenant_id, subject.id, "registrations", registration_id)
            await self._storage.put(key, command.capture)

            registration = FaceRegistration.create(
                id=registration_id,
                tenant_id=command.tenant_id,
                subject_id=subject.id,
                capture_object_key=key,
                now=now,
                replaces_registration_id=replaces,
            )
            if command.liveness is not None:
                registration.liveness_challenge_id, _ = await consume_liveness(
                    uow,
                    self._storage,
                    submission=command.liveness,
                    tenant_id=command.tenant_id,
                    subject_id=subject.id,
                    external_subject_id=command.external_subject_id,
                    purpose=LivenessPurpose.REGISTRATION,
                    by_id=registration.id,
                    clock=self._clock,
                )
            await uow.face_registrations.save(registration)
            await record_event(
                uow,
                self._new_id,
                tenant_id=command.tenant_id,
                aggregate_type="face_registration",
                aggregate_id=registration.id,
                event_type="FACE_REGISTRATION_CREATED",
                occurred_at=now,
                data={"replaces": str(replaces)} if replaces else None,
            )
            await uow.commit()

        await self._queue.enqueue_face_registration(registration.id)
        return FaceRegistrationAccepted(
            registration_id=registration.id,
            external_subject_id=command.external_subject_id,
            status=registration.status,
        )
