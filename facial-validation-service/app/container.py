"""Composition root: único lugar que conhece as implementações concretas."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from arq.connections import ArqRedis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.application.dto import CapturePolicy, LivenessIntakePolicy
from app.application.ports import (
    CaptureStorage,
    TaskQueue,
    TemplateCipher,
    UnitOfWorkFactory,
    WebhookSender,
)
from app.application.use_cases import (
    AuthenticateTenant,
    ConfigureTenantWebhook,
    CreateVerification,
    DeliverWebhooks,
    DisableTenantWebhook,
    GetFaceRegistration,
    GetSubjectFace,
    GetVerification,
    ProcessFaceRegistration,
    ProcessVerification,
    RegisterFace,
)
from app.application.use_cases.capture_analysis import FaceAnalysisPipeline
from app.application.use_cases.liveness import CreateLivenessSession
from app.config import Settings
from app.domain.services import FaceMatchPolicyRegistry
from app.domain.value_objects import CaptureRetention
from app.infrastructure.biometric import build_biometric_setup
from app.infrastructure.database.session import (
    SqlAlchemyUnitOfWork,
    check_database,
    create_engine,
)
from app.infrastructure.queue.arq_queue import ArqFrameObserver, ArqTaskQueue, create_queue_pool
from app.infrastructure.security.template_cipher import AesGcmTemplateCipher
from app.infrastructure.storage.s3 import S3CaptureStorage
from app.infrastructure.webhooks import HttpxWebhookSender
from app.interfaces.http.serializers import HttpResultRenderer


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class Container:
    settings: Settings
    uow_factory: UnitOfWorkFactory
    storage: CaptureStorage
    queue: TaskQueue
    pipeline: FaceAnalysisPipeline
    cipher: TemplateCipher
    policies: FaceMatchPolicyRegistry
    readiness_checks: dict[str, Callable[[], Awaitable[None]]] = field(default_factory=dict)
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    # Carga dos modelos biométricos: só o worker chama start_inference(); a API não.
    inference_starters: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    loaded_models: tuple[str, ...] = ()
    # Captura guiada da página /dev/liveness: só existe em APP_ENV=local.
    frame_observer: ArqFrameObserver | None = None
    webhook_sender: WebhookSender | None = None
    clock: SystemClock = field(default_factory=SystemClock)

    @property
    def capture_policy(self) -> CapturePolicy:
        return CapturePolicy(max_bytes=self.settings.max_capture_bytes)

    @property
    def liveness_intake(self) -> LivenessIntakePolicy:
        s = self.settings
        return LivenessIntakePolicy(
            # Com o liveness ativo exigido, a API recusa já na entrada o que viria sem prova.
            required=s.liveness_provider == "active" and s.liveness_requirement == "REQUIRED",
            min_frames=s.liveness_min_frames,
            max_frames=s.liveness_max_frames,
            frame_max_bytes=s.liveness_frame_max_bytes,
        )

    def create_liveness_session(self) -> CreateLivenessSession:
        return CreateLivenessSession(
            uow_factory=self.uow_factory,
            clock=self.clock,
            new_id=uuid.uuid4,
            ttl=timedelta(seconds=self.settings.liveness_session_ttl_seconds),
            steps=self.settings.liveness_challenge_steps,
            intake=self.liveness_intake,
        )

    def authenticate_tenant(self) -> AuthenticateTenant:
        return AuthenticateTenant(uow_factory=self.uow_factory)

    def register_face(self) -> RegisterFace:
        return RegisterFace(
            uow_factory=self.uow_factory,
            storage=self.storage,
            queue=self.queue,
            clock=self.clock,
            new_id=uuid.uuid4,
            capture_policy=self.capture_policy,
            liveness_intake=self.liveness_intake,
        )

    def create_verification(self) -> CreateVerification:
        return CreateVerification(
            uow_factory=self.uow_factory,
            storage=self.storage,
            queue=self.queue,
            clock=self.clock,
            new_id=uuid.uuid4,
            capture_policy=self.capture_policy,
            ttl=timedelta(seconds=self.settings.verification_ttl_seconds),
            liveness_intake=self.liveness_intake,
        )

    def get_verification(self) -> GetVerification:
        return GetVerification(uow_factory=self.uow_factory)

    def get_face_registration(self) -> GetFaceRegistration:
        return GetFaceRegistration(uow_factory=self.uow_factory)

    def get_subject_face(self) -> GetSubjectFace:
        return GetSubjectFace(uow_factory=self.uow_factory)

    def process_face_registration(self) -> ProcessFaceRegistration:
        return ProcessFaceRegistration(
            uow_factory=self.uow_factory,
            storage=self.storage,
            pipeline=self.pipeline,
            cipher=self.cipher,
            clock=self.clock,
            new_id=uuid.uuid4,
            queue=self.queue,
            capture_retention=CaptureRetention(self.settings.capture_retention),
        )

    def process_verification(self) -> ProcessVerification:
        return ProcessVerification(
            uow_factory=self.uow_factory,
            storage=self.storage,
            pipeline=self.pipeline,
            cipher=self.cipher,
            policies=self.policies,
            clock=self.clock,
            new_id=uuid.uuid4,
            queue=self.queue,
            capture_retention=CaptureRetention(self.settings.capture_retention),
        )

    def deliver_webhooks(self) -> DeliverWebhooks:
        if self.webhook_sender is None:
            raise RuntimeError("webhook_sender não configurado neste container")
        return DeliverWebhooks(
            uow_factory=self.uow_factory,
            sender=self.webhook_sender,
            cipher=self.cipher,
            renderer=HttpResultRenderer(),
            clock=self.clock,
        )

    def configure_tenant_webhook(self) -> ConfigureTenantWebhook:
        return ConfigureTenantWebhook(
            uow_factory=self.uow_factory, cipher=self.cipher, clock=self.clock
        )

    def disable_tenant_webhook(self) -> DisableTenantWebhook:
        return DisableTenantWebhook(uow_factory=self.uow_factory, clock=self.clock)

    async def start_inference(self) -> None:
        for start in self.inference_starters:
            await start()

    async def close(self) -> None:
        for closer in reversed(self.closers):
            await closer()


async def build_container(settings: Settings) -> Container:
    # Valida a configuração biométrica antes de abrir conexões (falha rápida).
    biometric = build_biometric_setup(settings)
    engine: AsyncEngine = create_engine(settings.database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    storage = S3CaptureStorage(
        bucket=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint,
        region=settings.s3_region,
        access_key=settings.s3_access_key.get_secret_value(),
        secret_key=settings.s3_secret_key.get_secret_value(),
    )
    if settings.s3_auto_create_bucket and settings.app_env in ("local", "test"):
        await storage.ensure_bucket()

    pool: ArqRedis = await create_queue_pool(settings.redis_url.get_secret_value())

    async def check_redis() -> None:
        await pool.ping()

    webhook_sender = HttpxWebhookSender(timeout_seconds=settings.webhook_timeout_seconds)

    return Container(
        settings=settings,
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        storage=storage,
        queue=ArqTaskQueue(pool),
        pipeline=biometric.pipeline,
        cipher=AesGcmTemplateCipher(settings.template_encryption_key.get_secret_value()),
        policies=biometric.policies,
        readiness_checks={
            "database": lambda: check_database(engine),
            "redis": check_redis,
            "storage": storage.check,
        },
        closers=[engine.dispose, pool.aclose, biometric.close, webhook_sender.close],
        webhook_sender=webhook_sender,
        inference_starters=[biometric.start],
        loaded_models=biometric.loaded_models,
        frame_observer=ArqFrameObserver(pool) if settings.app_env == "local" else None,
    )
