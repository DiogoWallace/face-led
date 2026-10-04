"""Dublês de teste em memória.

`BiometricScript` e os componentes `Scripted*` NÃO são motores biométricos:
devolvem respostas pré-programadas para testar a orquestração dos casos de uso.
Nenhum teste que os utiliza valida reconhecimento facial real (ver tests/biometric/).
"""

import copy
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.ports import (
    CaptureStorageError,
    FaceAnalysisComponents,
    TaskQueueError,
    WebhookTransportError,
)
from app.domain.entities import (
    FaceRegistration,
    LivenessChallenge,
    LivenessSession,
    Subject,
    Tenant,
    Verification,
    VerificationEvent,
    WebhookDelivery,
    WebhookDeliveryStatus,
)
from app.domain.exceptions import DuplicateIdempotencyKey
from app.domain.value_objects import (
    BoundingBox,
    CaptureData,
    DetectedFace,
    FaceDetectionResult,
    FaceEmbedding,
    FaceLandmarks,
    LivenessEvidence,
    LivenessResult,
    LivenessVerdict,
    Point,
    ProcessStatus,
    QualityMeasurements,
)

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FixedClock:
    def __init__(self, now: datetime | None = None) -> None:
        self.current = now or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


@dataclass
class InMemoryStore:
    tenants: dict[UUID, Tenant] = field(default_factory=dict)
    subjects: dict[UUID, Subject] = field(default_factory=dict)
    registrations: dict[UUID, FaceRegistration] = field(default_factory=dict)
    verifications: dict[UUID, Verification] = field(default_factory=dict)
    liveness: list[LivenessSession] = field(default_factory=list)
    events: list[VerificationEvent] = field(default_factory=list)
    webhooks: dict[UUID, WebhookDelivery] = field(default_factory=dict)
    challenges: dict[UUID, LivenessChallenge] = field(default_factory=dict)


class _Repo:
    def __init__(self, uow: "InMemoryUnitOfWork") -> None:
        self._uow = uow

    @property
    def _data(self) -> InMemoryStore:
        return self._uow.pending


class _Tenants(_Repo):
    async def get_by_api_key_hash(self, api_key_hash: str) -> Tenant | None:
        return next(
            (t for t in self._data.tenants.values() if t.api_key_hash == api_key_hash), None
        )

    async def get(self, tenant_id: UUID) -> Tenant | None:
        return copy.deepcopy(self._data.tenants.get(tenant_id))

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return next((copy.deepcopy(t) for t in self._data.tenants.values() if t.slug == slug), None)

    async def add(self, tenant: Tenant) -> None:
        self._data.tenants[tenant.id] = copy.deepcopy(tenant)

    async def save(self, tenant: Tenant) -> None:
        self._data.tenants[tenant.id] = copy.deepcopy(tenant)


class _Subjects(_Repo):
    async def get_by_external_id(self, tenant_id: UUID, external_id: str) -> Subject | None:
        return next(
            (
                copy.deepcopy(s)
                for s in self._data.subjects.values()
                if s.tenant_id == tenant_id and s.external_id == external_id
            ),
            None,
        )

    async def get(self, tenant_id: UUID, subject_id: UUID) -> Subject | None:
        s = self._data.subjects.get(subject_id)
        return copy.deepcopy(s) if s and s.tenant_id == tenant_id else None

    async def add(self, subject: Subject) -> None:
        self._data.subjects[subject.id] = copy.deepcopy(subject)


class _Registrations(_Repo):
    async def get(self, registration_id: UUID) -> FaceRegistration | None:
        return copy.deepcopy(self._data.registrations.get(registration_id))

    async def get_for_tenant(
        self, tenant_id: UUID, registration_id: UUID
    ) -> FaceRegistration | None:
        r = self._data.registrations.get(registration_id)
        return copy.deepcopy(r) if r and r.tenant_id == tenant_id else None

    async def get_latest_for_subject(
        self, tenant_id: UUID, subject_id: UUID
    ) -> FaceRegistration | None:
        mine = [
            r
            for r in self._data.registrations.values()
            if r.tenant_id == tenant_id and r.subject_id == subject_id
        ]
        latest = max(mine, key=lambda r: (r.created_at, str(r.id)), default=None)
        return copy.deepcopy(latest)

    async def get_active_for_subject(
        self, tenant_id: UUID, subject_id: UUID
    ) -> FaceRegistration | None:
        return next(
            (
                copy.deepcopy(r)
                for r in self._data.registrations.values()
                if r.tenant_id == tenant_id
                and r.subject_id == subject_id
                and r.status is ProcessStatus.APPROVED
                and r.superseded_at is None
            ),
            None,
        )

    async def save(self, registration: FaceRegistration) -> None:
        self._data.registrations[registration.id] = copy.deepcopy(registration)


class _Verifications(_Repo):
    async def get(self, verification_id: UUID) -> Verification | None:
        return copy.deepcopy(self._data.verifications.get(verification_id))

    async def get_for_tenant(self, tenant_id: UUID, verification_id: UUID) -> Verification | None:
        v = self._data.verifications.get(verification_id)
        return copy.deepcopy(v) if v and v.tenant_id == tenant_id else None

    async def get_by_idempotency_key(self, tenant_id: UUID, key: str) -> Verification | None:
        return next(
            (
                copy.deepcopy(v)
                for v in self._data.verifications.values()
                if v.tenant_id == tenant_id and v.idempotency_key == key
            ),
            None,
        )

    async def save(self, verification: Verification) -> None:
        for other in self._data.verifications.values():
            if (
                other.id != verification.id
                and other.tenant_id == verification.tenant_id
                and other.idempotency_key == verification.idempotency_key
            ):
                raise DuplicateIdempotencyKey("duplicada")
        self._data.verifications[verification.id] = copy.deepcopy(verification)


class _Liveness(_Repo):
    async def add(self, session: LivenessSession) -> None:
        self._data.liveness.append(session)


class _Events(_Repo):
    async def add(self, event: VerificationEvent) -> None:
        self._data.events.append(event)


class _Challenges(_Repo):
    async def add(self, challenge: LivenessChallenge) -> None:
        self._data.challenges[challenge.id] = copy.deepcopy(challenge)

    async def get(self, challenge_id: UUID) -> LivenessChallenge | None:
        return copy.deepcopy(self._data.challenges.get(challenge_id))

    async def get_for_tenant(
        self, tenant_id: UUID, challenge_id: UUID, *, for_update: bool = False
    ) -> LivenessChallenge | None:
        c = self._data.challenges.get(challenge_id)
        return copy.deepcopy(c) if c and c.tenant_id == tenant_id else None

    async def save(self, challenge: LivenessChallenge) -> None:
        self._data.challenges[challenge.id] = copy.deepcopy(challenge)


class _Webhooks(_Repo):
    async def add(self, delivery: WebhookDelivery) -> None:
        self._data.webhooks[delivery.id] = copy.deepcopy(delivery)

    async def claim_due(self, now: datetime, lease: timedelta, limit: int) -> list[WebhookDelivery]:
        due = sorted(
            (
                d
                for d in self._data.webhooks.values()
                if d.status is WebhookDeliveryStatus.PENDING and d.next_attempt_at <= now
            ),
            key=lambda d: d.next_attempt_at,
        )[:limit]
        for d in due:
            d.claim(now, lease)
        return [copy.deepcopy(d) for d in due]

    async def save(self, delivery: WebhookDelivery) -> None:
        self._data.webhooks[delivery.id] = copy.deepcopy(delivery)


class InMemoryUnitOfWork:
    """Transação simulada: altera uma cópia e só publica no commit."""

    def __init__(self, store: InMemoryStore) -> None:
        self.store = store
        self.tenants = _Tenants(self)
        self.subjects = _Subjects(self)
        self.face_registrations = _Registrations(self)
        self.verifications = _Verifications(self)
        self.liveness_sessions = _Liveness(self)
        self.events = _Events(self)
        self.webhook_deliveries = _Webhooks(self)
        self.liveness_challenges = _Challenges(self)

    async def __aenter__(self) -> "InMemoryUnitOfWork":
        self.pending = copy.deepcopy(self.store)
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def commit(self) -> None:
        self.store.__dict__.update(copy.deepcopy(self.pending).__dict__)

    async def rollback(self) -> None:
        self.pending = copy.deepcopy(self.store)


class InMemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, CaptureData] = {}
        self.fail = False
        self.fail_delete = False

    async def put(self, key: str, capture: CaptureData) -> None:
        if self.fail:
            raise CaptureStorageError("indisponível")
        self.objects[key] = capture

    async def get(self, key: str) -> CaptureData:
        if self.fail or key not in self.objects:
            raise CaptureStorageError("indisponível")
        return self.objects[key]

    async def delete(self, key: str) -> None:
        if self.fail_delete:
            raise CaptureStorageError("indisponível")
        self.objects.pop(key, None)

    async def temporary_url(self, key: str, expires_in_seconds: int) -> str:
        return f"memory://{key}?expires={expires_in_seconds}"

    async def check(self) -> None:
        if self.fail:
            raise CaptureStorageError("indisponível")


class RecordingQueue:
    def __init__(self) -> None:
        self.registrations: list[UUID] = []
        self.verifications: list[UUID] = []
        self.webhooks: list[UUID] = []
        self.fail_webhooks = False

    async def enqueue_webhook_delivery(self, delivery_id: UUID) -> None:
        if self.fail_webhooks:
            raise TaskQueueError("indisponível")
        self.webhooks.append(delivery_id)

    async def enqueue_face_registration(self, registration_id: UUID) -> None:
        self.registrations.append(registration_id)

    async def enqueue_verification(self, verification_id: UUID) -> None:
        self.verifications.append(verification_id)


class XorCipher:
    """Cifra de brinquedo apenas para testes de orquestração (reversível e com AAD)."""

    def encrypt(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        return associated_data + b"|" + bytes(b ^ 0x5A for b in plaintext)

    def decrypt(self, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        prefix = associated_data + b"|"
        assert ciphertext.startswith(prefix), "AAD divergente"
        return bytes(b ^ 0x5A for b in ciphertext[len(prefix) :])


@dataclass
class BiometricScript:
    """Roteiro compartilhado pelos componentes Scripted*. Não analisa imagem nenhuma.

    Os nomes de etapa em `calls`/`raise_on` são: detect_face, assess_quality,
    check_liveness, generate_embedding, compare.
    """

    face_count: int = 1
    face_px: float = 100.0
    background_faces: int = 0  # rostos pequenos (20 px) ao fundo
    image_size: tuple[int, int] = (640, 480)
    quality_ok: bool = True
    liveness: LivenessVerdict = LivenessVerdict.LIVE
    embedding: bytes = b"scripted-embedding"
    model_name: str = "scripted-model"
    model_version: str = "1"
    similarity: float = 0.9
    raise_on: str | None = None
    exception: type[Exception] | None = None
    calls: list[str] = field(default_factory=list)
    liveness_evidence: list[LivenessEvidence] = field(default_factory=list)

    def step(self, name: str) -> None:
        self.calls.append(name)
        if self.raise_on == name and self.exception is not None:
            raise self.exception("scripted failure")

    def components(self) -> FaceAnalysisComponents:
        return FaceAnalysisComponents(
            detector=ScriptedDetector(self),
            quality_assessor=ScriptedQualityAssessor(self),
            embedder=ScriptedEmbedder(self),
            comparator=ScriptedComparator(self),
            liveness=ScriptedLiveness(self),
        )


def scripted_face(px: float, offset: float = 0.0) -> DetectedFace:
    p = Point(offset + px / 2, px / 2)
    return DetectedFace(
        box=BoundingBox(x=offset, y=0, width=px, height=px),
        confidence=0.99,
        landmarks=FaceLandmarks(right_eye=p, left_eye=p, nose_tip=p, mouth_right=p, mouth_left=p),
    )


class _Scripted:
    def __init__(self, script: BiometricScript) -> None:
        self.script = script


class ScriptedDetector(_Scripted):
    name = "scripted-detector"

    async def detect(self, capture: CaptureData) -> FaceDetectionResult:
        self.script.step("detect_face")
        w, h = self.script.image_size
        faces = tuple(
            scripted_face(self.script.face_px, offset=i * self.script.face_px)
            for i in range(self.script.face_count)
        ) + tuple(
            scripted_face(20.0, offset=400 + i * 30) for i in range(self.script.background_faces)
        )
        return FaceDetectionResult(faces=faces, image_width=w, image_height=h)


class ScriptedQualityAssessor(_Scripted):
    name = "scripted-quality"

    async def measure(self, capture: CaptureData, face: DetectedFace) -> QualityMeasurements:
        self.script.step("assess_quality")
        return QualityMeasurements(
            sharpness=100.0 if self.script.quality_ok else 1.0, brightness=120.0
        )


class ScriptedLiveness(_Scripted):
    name = "scripted-liveness"

    async def check(self, evidence: LivenessEvidence) -> LivenessResult:
        self.script.step("check_liveness")
        self.script.liveness_evidence.append(evidence)
        return LivenessResult(verdict=self.script.liveness, score=0.5)


class ScriptedEmbedder(_Scripted):
    name = "scripted-embedder"

    async def embed(self, capture: CaptureData, face: DetectedFace) -> FaceEmbedding:
        self.script.step("generate_embedding")
        return FaceEmbedding(
            vector=self.script.embedding,
            model_name=self.script.model_name,
            model_version=self.script.model_version,
        )


class ScriptedComparator(_Scripted):
    name = "scripted-comparator"

    async def similarity(self, probe: FaceEmbedding, reference: FaceEmbedding) -> float:
        self.script.step("compare")
        return self.script.similarity


def new_id() -> UUID:
    return uuid.uuid4()


@dataclass
class SentWebhook:
    url: str
    secret: str
    event_id: UUID
    event_type: str
    body: bytes


class FakeWebhookSender:
    """Grava o que seria enviado. Roteiro de respostas: int = status HTTP, str = erro de rede."""

    def __init__(self) -> None:
        self.sent: list[SentWebhook] = []
        self.responses: list[int | str] = []

    async def deliver(
        self, *, url: str, secret: str, event_id: UUID, event_type: str, body: bytes
    ) -> int:
        self.sent.append(SentWebhook(url, secret, event_id, event_type, body))
        response = self.responses.pop(0) if self.responses else 200
        if isinstance(response, str):
            raise WebhookTransportError(response)
        return response
