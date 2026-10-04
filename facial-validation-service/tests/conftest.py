import os
import uuid
from datetime import timedelta

import pytest

from app.application.dto import CapturePolicy, LivenessIntakePolicy
from app.application.use_cases import (
    ConfigureTenantWebhook,
    CreateVerification,
    DeliverWebhooks,
    GetFaceRegistration,
    GetSubjectFace,
    GetVerification,
    ProcessFaceRegistration,
    ProcessVerification,
    RegisterFace,
)
from app.application.use_cases.capture_analysis import FaceAnalysisPipeline
from app.application.use_cases.liveness import CreateLivenessSession
from app.domain.entities import Tenant, TenantStatus
from app.domain.services import (
    FaceMatchPolicy,
    FaceMatchPolicyRegistry,
    LivenessRequirement,
    QualityGate,
    QualityRequirements,
)
from app.domain.value_objects import CaptureRetention
from app.interfaces.http.serializers import HttpResultRenderer
from tests.fakes import (
    BiometricScript,
    FakeWebhookSender,
    FixedClock,
    InMemoryStorage,
    InMemoryStore,
    InMemoryUnitOfWork,
    RecordingQueue,
    XorCipher,
)

# O container `api` recebe o .env de quem roda os testes (ex.: LIVENESS_PROVIDER=active
# para testar a página local). Os testes não podem depender dessa configuração.
_DEVELOPER_ENV_PREFIXES = (
    "BIOMETRIC_",
    "LIVENESS_",
    "FACE_MATCH_",
    "QUALITY_",
    "CAPTURE_RETENTION",
)
_DEVELOPER_ENV_KEEP = frozenset({"BIOMETRIC_MODELS_DIR"})


@pytest.fixture(autouse=True)
def _isolate_developer_env(request, monkeypatch):
    # `models` e `evaluation` avaliam justamente os componentes configurados.
    if request.node.get_closest_marker("models") or request.node.get_closest_marker("evaluation"):
        return
    for name in list(os.environ):
        if name.startswith(_DEVELOPER_ENV_PREFIXES) and name not in _DEVELOPER_ENV_KEEP:
            monkeypatch.delenv(name)


class World:
    """Monta os casos de uso com dublês em memória."""

    def __init__(self) -> None:
        self.store = InMemoryStore()
        self.storage = InMemoryStorage()
        self.queue = RecordingQueue()
        self.clock = FixedClock()
        self.script = BiometricScript()
        # Requisitos SOMENTE de teste (não são calibração): exercitam o QualityGate.
        self.quality_gate = QualityGate(QualityRequirements(min_face_px=40, min_sharpness=10))
        self.liveness_requirement = LivenessRequirement.REQUIRED
        self.capture_retention = CaptureRetention.KEEP
        self.webhook_sender = FakeWebhookSender()
        # Liveness ativo (ADR-010): opcional por padrão; testes ligam `required`.
        self.liveness_intake = LivenessIntakePolicy(min_frames=3, max_frames=10)
        self.cipher = XorCipher()
        self.policies = FaceMatchPolicyRegistry()
        self.policies.register(
            FaceMatchPolicy(
                version="test-v1",
                model_name="scripted-model",
                model_version="1",
                min_similarity=0.8,
            ),
            activate=True,
        )
        self.capture_policy = CapturePolicy(max_bytes=1024)
        now = self.clock.now()
        self.tenant = Tenant(
            id=uuid.uuid4(),
            name="Tenant A",
            slug="tenant-a",
            status=TenantStatus.ACTIVE,
            api_key_hash="x",
            created_at=now,
            updated_at=now,
        )
        self.store.tenants[self.tenant.id] = self.tenant

    def pipeline(self) -> FaceAnalysisPipeline:
        return FaceAnalysisPipeline(
            components=self.script.components(),
            quality_gate=self.quality_gate,
            liveness_requirement=self.liveness_requirement,
        )

    def uow(self) -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(self.store)

    def register_face(self) -> RegisterFace:
        return RegisterFace(
            uow_factory=self.uow,
            storage=self.storage,
            queue=self.queue,
            clock=self.clock,
            new_id=uuid.uuid4,
            capture_policy=self.capture_policy,
            liveness_intake=self.liveness_intake,
        )

    def process_registration(self) -> ProcessFaceRegistration:
        return ProcessFaceRegistration(
            uow_factory=self.uow,
            storage=self.storage,
            pipeline=self.pipeline(),
            cipher=self.cipher,
            clock=self.clock,
            new_id=uuid.uuid4,
            queue=self.queue,
            capture_retention=self.capture_retention,
        )

    def create_verification(self) -> CreateVerification:
        return CreateVerification(
            uow_factory=self.uow,
            storage=self.storage,
            queue=self.queue,
            clock=self.clock,
            new_id=uuid.uuid4,
            capture_policy=self.capture_policy,
            ttl=timedelta(minutes=15),
            liveness_intake=self.liveness_intake,
        )

    def create_liveness_session(self) -> CreateLivenessSession:
        return CreateLivenessSession(
            uow_factory=self.uow,
            clock=self.clock,
            new_id=uuid.uuid4,
            ttl=timedelta(minutes=2),
            steps=3,
            intake=self.liveness_intake,
        )

    def process_verification(self) -> ProcessVerification:
        return ProcessVerification(
            uow_factory=self.uow,
            storage=self.storage,
            pipeline=self.pipeline(),
            cipher=self.cipher,
            policies=self.policies,
            clock=self.clock,
            new_id=uuid.uuid4,
            queue=self.queue,
            capture_retention=self.capture_retention,
        )

    def deliver_webhooks(self) -> DeliverWebhooks:
        return DeliverWebhooks(
            uow_factory=self.uow,
            sender=self.webhook_sender,
            cipher=self.cipher,
            renderer=HttpResultRenderer(),
            clock=self.clock,
        )

    def configure_webhook(self) -> ConfigureTenantWebhook:
        return ConfigureTenantWebhook(uow_factory=self.uow, cipher=self.cipher, clock=self.clock)

    def get_face_registration(self) -> GetFaceRegistration:
        return GetFaceRegistration(uow_factory=self.uow)

    def get_subject_face(self) -> GetSubjectFace:
        return GetSubjectFace(uow_factory=self.uow)

    def get_verification(self) -> GetVerification:
        return GetVerification(uow_factory=self.uow)


@pytest.fixture
def world() -> World:
    return World()
