from datetime import timedelta
from uuid import UUID

from app.application.dto import (
    CapturePolicy,
    CreateVerificationCommand,
    LivenessIntakePolicy,
    VerificationView,
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
from app.domain.entities import LivenessPurpose, Subject, Verification
from app.domain.exceptions import (
    DuplicateIdempotencyKey,
    IdempotencyConflict,
    SubjectNotEnrolled,
    SubjectNotFound,
    VerificationNotFound,
)
from app.domain.repositories import UnitOfWork


def to_view(verification: Verification, subject: Subject, *, replayed: bool = False):
    return VerificationView(
        verification_id=verification.id,
        external_subject_id=subject.external_id,
        status=verification.status,
        decision=verification.decision,
        reason=verification.reason,
        policy_version=verification.policy_version,
        created_at=verification.created_at,
        updated_at=verification.updated_at,
        expires_at=verification.expires_at,
        replayed=replayed,
        quality_issues=verification.quality_issues,
    )


class CreateVerification:
    """Recebe nova captura e agenda a validação 1:1. Idempotente por Idempotency-Key."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        storage: CaptureStorage,
        queue: TaskQueue,
        clock: Clock,
        new_id: IdGenerator,
        capture_policy: CapturePolicy,
        ttl: timedelta,
        liveness_intake: LivenessIntakePolicy = NO_LIVENESS_INTAKE,
    ) -> None:
        self._liveness_intake = liveness_intake
        self._uow_factory = uow_factory
        self._storage = storage
        self._queue = queue
        self._clock = clock
        self._new_id = new_id
        self._capture_policy = capture_policy
        self._ttl = ttl

    async def execute(self, command: CreateVerificationCommand) -> VerificationView:
        validate_capture(command.capture, self._capture_policy)
        check_liveness_submission(command.liveness, self._liveness_intake)

        async with self._uow_factory() as uow:
            subject = await uow.subjects.get_by_external_id(
                command.tenant_id, command.external_subject_id
            )
            if subject is None:
                raise SubjectNotFound("subject não encontrado")

            replay = await self._replay(uow, command, subject)
            if replay is not None:
                return replay

            if not await uow.face_registrations.get_active_for_subject(
                command.tenant_id, subject.id
            ):
                raise SubjectNotEnrolled("subject não possui cadastro facial ativo")

            now = self._clock.now()
            verification_id = self._new_id()
            key = capture_key(command.tenant_id, subject.id, "verifications", verification_id)
            await self._storage.put(key, command.capture)

            verification = Verification.create(
                id=verification_id,
                tenant_id=command.tenant_id,
                subject_id=subject.id,
                idempotency_key=command.idempotency_key,
                capture_object_key=key,
                expires_at=now + self._ttl,
                now=now,
            )
            frame_keys: tuple[str, ...] = ()
            if command.liveness is not None:
                verification.liveness_challenge_id, frame_keys = await consume_liveness(
                    uow,
                    self._storage,
                    submission=command.liveness,
                    tenant_id=command.tenant_id,
                    subject_id=subject.id,
                    external_subject_id=command.external_subject_id,
                    purpose=LivenessPurpose.VERIFICATION,
                    by_id=verification.id,
                    clock=self._clock,
                )
            try:
                await uow.verifications.save(verification)
                await record_event(
                    uow,
                    self._new_id,
                    tenant_id=command.tenant_id,
                    aggregate_type="verification",
                    aggregate_id=verification.id,
                    event_type="VERIFICATION_CREATED",
                    occurred_at=now,
                )
                await uow.commit()
            except DuplicateIdempotencyKey:
                await uow.rollback()  # desfaz também o consumo da sessão de liveness
                for orphan in (key, *frame_keys):
                    await self._storage.delete(orphan)
                replay = await self._replay(uow, command, subject)
                if replay is None:
                    raise
                return replay

        await self._queue.enqueue_verification(verification.id)
        return to_view(verification, subject)

    async def _replay(
        self, uow: UnitOfWork, command: CreateVerificationCommand, subject: Subject
    ) -> VerificationView | None:
        existing = await uow.verifications.get_by_idempotency_key(
            command.tenant_id, command.idempotency_key
        )
        if existing is None:
            return None
        if existing.subject_id != subject.id:
            raise IdempotencyConflict("Idempotency-Key já usada para outro subject")
        return to_view(existing, subject, replayed=True)


class GetVerification:
    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, tenant_id: UUID, verification_id: UUID) -> VerificationView:
        async with self._uow_factory() as uow:
            verification = await uow.verifications.get_for_tenant(tenant_id, verification_id)
            if verification is None:
                raise VerificationNotFound("validação não encontrada")
            subject = await uow.subjects.get(tenant_id, verification.subject_id)
            if subject is None:
                raise VerificationNotFound("validação não encontrada")
            return to_view(verification, subject)
