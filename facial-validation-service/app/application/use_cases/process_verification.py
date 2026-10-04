import logging
from uuid import UUID

from app.application.ports import (
    BiometricProviderError,
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
from app.application.use_cases.process_face_registration import template_aad
from app.application.use_cases.webhooks import schedule_result_webhook, trigger_delivery
from app.domain.entities import LivenessPurpose, LivenessSession, Verification, WebhookEventType
from app.domain.exceptions import PolicyModelMismatch
from app.domain.services import FaceMatchPolicyRegistry, MatchDecision, MatchPurpose
from app.domain.value_objects import CaptureRetention, ErrorReason, FaceEmbedding, ProcessStatus

logger = logging.getLogger(__name__)


class ProcessVerification:
    """Worker: pipeline de captura -> referência -> comparação -> FaceMatchPolicy."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        storage: CaptureStorage,
        pipeline: FaceAnalysisPipeline,
        cipher: TemplateCipher,
        policies: FaceMatchPolicyRegistry,
        clock: Clock,
        new_id: IdGenerator,
        queue: TaskQueue,
        capture_retention: CaptureRetention = CaptureRetention.KEEP,
    ) -> None:
        self._queue = queue
        self._uow_factory = uow_factory
        self._storage = storage
        self._pipeline = pipeline
        self._cipher = cipher
        self._policies = policies
        self._clock = clock
        self._new_id = new_id
        self._capture_retention = capture_retention

    async def execute(self, verification_id: UUID) -> ProcessStatus | None:
        async with self._uow_factory() as uow:
            verification = await uow.verifications.get(verification_id)
            if verification is None or verification.status is not ProcessStatus.CREATED:
                return verification.status if verification else None
            now = self._clock.now()
            if verification.is_expired(now):
                verification.expire(now)
            else:
                verification.start_processing(now)
            await uow.verifications.save(verification)
            await uow.commit()
        if verification.status is ProcessStatus.EXPIRED:
            return await self._finish(verification, None)

        policy = self._policies.active_for(MatchPurpose.SELFIE_VS_REFERENCE)
        if policy is None:
            verification.fail(ErrorReason.POLICY_NOT_CONFIGURED, self._clock.now())
            return await self._finish(verification, None)

        analysis = await self._analyze(verification)
        now = self._clock.now()
        if analysis.rejection is not None:
            verification.reject(analysis.rejection, now)
            return await self._finish(verification, analysis)
        if analysis.error is not None or analysis.embedding is None:
            verification.fail(analysis.error or ErrorReason.INTERNAL_ERROR, now)
            return await self._finish(verification, analysis)

        async with self._uow_factory() as uow:
            registration = await uow.face_registrations.get_active_for_subject(
                verification.tenant_id, verification.subject_id
            )
        if registration is None or registration.template is None:
            verification.fail(ErrorReason.REFERENCE_NOT_FOUND, self._clock.now())
            return await self._finish(verification, analysis)

        probe = analysis.embedding
        reference = FaceEmbedding(
            vector=self._cipher.decrypt(
                registration.template, associated_data=template_aad(registration)
            ),
            model_name=registration.model_name or "",
            model_version=registration.model_version or "",
        )
        if not probe.same_model_as(reference):
            verification.fail(ErrorReason.MODEL_MISMATCH, self._clock.now())
            return await self._finish(verification, analysis)

        try:
            similarity = await self._pipeline.components.comparator.similarity(probe, reference)
            decision = policy.decide(
                similarity, model_name=probe.model_name, model_version=probe.model_version
            )
        except PolicyModelMismatch:
            verification.fail(ErrorReason.MODEL_MISMATCH, self._clock.now())
            return await self._finish(verification, analysis)
        except BiometricProviderError:
            verification.fail(ErrorReason.PROVIDER_FAILURE, self._clock.now())
            return await self._finish(verification, analysis)

        verification.record_match(
            matched=decision is MatchDecision.MATCH,
            face_registration_id=registration.id,
            similarity=similarity,
            policy_version=policy.version,
            now=self._clock.now(),
        )
        return await self._finish(verification, analysis)

    async def _analyze(self, verification: Verification) -> CaptureAnalysis:
        if verification.capture_object_key is None:
            return CaptureAnalysis(error=ErrorReason.CAPTURE_UNAVAILABLE)
        try:
            capture = await self._storage.get(verification.capture_object_key)
            evidence, _ = await load_liveness_evidence(
                self._uow_factory, self._storage, verification.liveness_challenge_id, capture
            )
        except CaptureStorageError:
            return CaptureAnalysis(error=ErrorReason.CAPTURE_UNAVAILABLE)
        try:
            return await self._pipeline.analyze(capture, evidence)
        except Exception:
            logger.exception(
                "verification_unexpected_error",
                extra={"verification_id": str(verification.id)},
            )
            return CaptureAnalysis(error=ErrorReason.INTERNAL_ERROR)

    async def _finish(
        self, verification: Verification, analysis: CaptureAnalysis | None
    ) -> ProcessStatus:
        now = self._clock.now()
        if analysis is not None and analysis.quality is not None:
            verification.record_quality(analysis.quality.issues)
        async with self._uow_factory() as uow:
            if analysis is not None and analysis.liveness is not None:
                await uow.liveness_sessions.add(
                    LivenessSession.from_result(
                        id=self._new_id(),
                        tenant_id=verification.tenant_id,
                        subject_id=verification.subject_id,
                        purpose=LivenessPurpose.VERIFICATION,
                        reference_id=verification.id,
                        provider=self._pipeline.components.liveness.name,
                        result=analysis.liveness,
                        now=now,
                    )
                )
            await uow.verifications.save(verification)
            await record_event(
                uow,
                self._new_id,
                tenant_id=verification.tenant_id,
                aggregate_type="verification",
                aggregate_id=verification.id,
                event_type=f"VERIFICATION_{verification.status}",
                occurred_at=now,
                data={
                    "reason": verification.reason,
                    "liveness_detail": (
                        analysis.liveness.detail if analysis and analysis.liveness else None
                    ),
                    "policy_version": verification.policy_version,
                    "components": self._pipeline.components.describe(),
                    "quality": (
                        analysis.quality.as_audit_data() if analysis and analysis.quality else None
                    ),
                },
            )
            delivery_id = await schedule_result_webhook(
                uow,
                tenant_id=verification.tenant_id,
                event_type=WebhookEventType.VERIFICATION_COMPLETED,
                resource_id=verification.id,
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
            aggregate=verification,
            aggregate_type="verification",
            extra_keys=await liveness_frame_keys(
                self._uow_factory, verification.liveness_challenge_id
            ),
            save=lambda uow, v: uow.verifications.save(v),
        )
        logger.info(
            "verification_processed",
            extra={
                "verification_id": str(verification.id),
                "tenant_id": str(verification.tenant_id),
                "status": verification.status.value,
                "reason": verification.reason,
            },
        )
        return verification.status
