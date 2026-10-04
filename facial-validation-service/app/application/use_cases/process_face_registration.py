import logging
from uuid import UUID

from app.application.ports import (
    CaptureStorage,
    CaptureStorageError,
    Clock,
    IdGenerator,
    TaskQueue,
    TemplateCipher,
    UnitOfWorkFactory,
)
from app.application.use_cases._common import record_event, release_capture
from app.application.use_cases.capture_analysis import CaptureAnalysis, FaceAnalysisPipeline
from app.application.use_cases.liveness import liveness_frame_keys, load_liveness_evidence
from app.application.use_cases.webhooks import schedule_result_webhook, trigger_delivery
from app.domain.entities import (
    FaceRegistration,
    LivenessPurpose,
    LivenessSession,
    WebhookEventType,
)
from app.domain.value_objects import CaptureRetention, ErrorReason, ProcessStatus

logger = logging.getLogger(__name__)


def _str(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def template_aad(registration: FaceRegistration) -> bytes:
    """Dados associados que amarram o template cifrado ao seu tenant/subject."""
    return f"{registration.tenant_id}:{registration.subject_id}:{registration.id}".encode()


class ProcessFaceRegistration:
    """Worker: detecção -> qualidade -> liveness -> embedding -> salvar referência."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        storage: CaptureStorage,
        pipeline: FaceAnalysisPipeline,
        cipher: TemplateCipher,
        clock: Clock,
        new_id: IdGenerator,
        queue: TaskQueue,
        capture_retention: CaptureRetention = CaptureRetention.KEEP,
    ) -> None:
        self._uow_factory = uow_factory
        self._queue = queue
        self._storage = storage
        self._pipeline = pipeline
        self._cipher = cipher
        self._clock = clock
        self._new_id = new_id
        self._capture_retention = capture_retention

    async def execute(self, registration_id: UUID) -> ProcessStatus | None:
        async with self._uow_factory() as uow:
            registration = await uow.face_registrations.get(registration_id)
            if registration is None or registration.status is not ProcessStatus.CREATED:
                # Reentrega da fila: processamento é idempotente.
                return registration.status if registration else None
            registration.start_processing(self._clock.now())
            await uow.face_registrations.save(registration)
            await uow.commit()

        analysis = await self._analyze(registration)
        now = self._clock.now()
        superseded_id: UUID | None = None
        registration.record_quality(analysis.quality.issues if analysis.quality else None)

        async with self._uow_factory() as uow:
            if analysis.liveness is not None:
                await uow.liveness_sessions.add(
                    LivenessSession.from_result(
                        id=self._new_id(),
                        tenant_id=registration.tenant_id,
                        subject_id=registration.subject_id,
                        purpose=LivenessPurpose.REGISTRATION,
                        reference_id=registration.id,
                        provider=self._pipeline.components.liveness.name,
                        result=analysis.liveness,
                        now=now,
                    )
                )

            if analysis.rejection is not None:
                registration.reject(analysis.rejection, now)
            elif analysis.error is not None or analysis.embedding is None:
                registration.fail(analysis.error or ErrorReason.INTERNAL_ERROR, now)
            elif (
                active := await uow.face_registrations.get_active_for_subject(
                    registration.tenant_id, registration.subject_id
                )
            ) is not None and not registration.is_replacement:
                # Cadastro inicial concorrente: outro concluiu primeiro; mantém o existente.
                registration.fail(ErrorReason.INTERNAL_ERROR, now)
            else:
                if active is not None:
                    # Recadastro (ADR-008): aposenta a referência atual (template apagado)
                    # ANTES de aprovar a nova — o índice único só admite uma ativa.
                    active.supersede(by=registration.id, now=now)
                    await uow.face_registrations.save(active)
                    await record_event(
                        uow,
                        self._new_id,
                        tenant_id=active.tenant_id,
                        aggregate_type="face_registration",
                        aggregate_id=active.id,
                        event_type="FACE_REGISTRATION_SUPERSEDED",
                        occurred_at=now,
                        data={"superseded_by": str(registration.id)},
                    )
                    superseded_id = active.id
                embedding = analysis.embedding
                registration.approve(
                    template=self._cipher.encrypt(
                        embedding.vector, associated_data=template_aad(registration)
                    ),
                    model_name=embedding.model_name,
                    model_version=embedding.model_version,
                    now=now,
                )

            await uow.face_registrations.save(registration)
            await record_event(
                uow,
                self._new_id,
                tenant_id=registration.tenant_id,
                aggregate_type="face_registration",
                aggregate_id=registration.id,
                event_type=f"FACE_REGISTRATION_{registration.status}",
                occurred_at=now,
                data={
                    "reason": registration.reason,
                    "liveness_detail": (
                        analysis.liveness.detail if analysis and analysis.liveness else None
                    ),
                    "components": self._pipeline.components.describe(),
                    "quality": analysis.quality.as_audit_data() if analysis.quality else None,
                    "replaces": _str(registration.replaces_registration_id),
                    "superseded": _str(superseded_id),
                },
            )
            delivery_id = await schedule_result_webhook(
                uow,
                tenant_id=registration.tenant_id,
                event_type=WebhookEventType.FACE_REGISTRATION_COMPLETED,
                resource_id=registration.id,
                new_id=self._new_id,
                clock=self._clock,
            )
            await uow.commit()

        await trigger_delivery(self._queue, delivery_id)

        await release_capture(
            retention=self._capture_retention,
            storage=self._storage,
            uow_factory=self._uow_factory,
            new_id=self._new_id,
            clock=self._clock,
            aggregate=registration,
            aggregate_type="face_registration",
            extra_keys=await liveness_frame_keys(
                self._uow_factory, registration.liveness_challenge_id
            ),
            save=lambda uow, r: uow.face_registrations.save(r),
        )
        logger.info(
            "face_registration_processed",
            extra={
                "registration_id": str(registration.id),
                "tenant_id": str(registration.tenant_id),
                "status": registration.status.value,
                "reason": registration.reason,
                "replacement": registration.is_replacement,
                "quality_issues": list(analysis.quality.issues) if analysis.quality else None,
            },
        )
        return registration.status

    async def _analyze(self, registration: FaceRegistration) -> CaptureAnalysis:
        if registration.capture_object_key is None:
            return CaptureAnalysis(error=ErrorReason.CAPTURE_UNAVAILABLE)
        try:
            capture = await self._storage.get(registration.capture_object_key)
            evidence, _ = await load_liveness_evidence(
                self._uow_factory, self._storage, registration.liveness_challenge_id, capture
            )
        except CaptureStorageError:
            return CaptureAnalysis(error=ErrorReason.CAPTURE_UNAVAILABLE)
        try:
            return await self._pipeline.analyze(capture, evidence)
        except Exception:
            logger.exception(
                "face_registration_unexpected_error",
                extra={"registration_id": str(registration.id)},
            )
            return CaptureAnalysis(error=ErrorReason.INTERNAL_ERROR)
