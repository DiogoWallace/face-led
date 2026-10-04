"""Liveness ativo próprio (ADR-010): abrir a sessão de desafio e consumi-la.

1. `CreateLivenessSession`: o servidor sorteia os passos e devolve a sessão
   (uso único, com prazo, amarrada a tenant + subject + finalidade).
2. O front captura quadros enquanto a pessoa executa os passos e manda, junto
   da selfie, `liveness_session_id` + quadros no cadastro/validação.
3. `consume_liveness` valida os quadros, guarda no storage e marca a sessão
   como usada NA MESMA transação do cadastro/validação.
4. O worker remonta a evidência (`load_liveness_evidence`) e o
   LivenessProvider decide.
"""

from datetime import timedelta
from uuid import UUID

from app.application.dto import (
    CapturePolicy,
    LivenessIntakePolicy,
    LivenessSessionView,
    LivenessSubmission,
)
from app.application.ports import (
    CaptureStorage,
    Clock,
    FaceDetector,
    IdGenerator,
    UnitOfWorkFactory,
)
from app.application.use_cases._common import capture_key, validate_capture
from app.domain.entities import LivenessChallenge, LivenessPurpose
from app.domain.exceptions import (
    InvalidCapture,
    LivenessEvidenceRequired,
    LivenessSessionNotFound,
)
from app.domain.repositories import UnitOfWork
from app.domain.services import FrameObservation, new_challenge, observe
from app.domain.value_objects import CaptureData, FaceDetectionResult, LivenessEvidence

# Padrão dos casos de uso: evidência opcional (liveness ativo desligado).
NO_LIVENESS_INTAKE = LivenessIntakePolicy()


class CreateLivenessSession:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        new_id: IdGenerator,
        ttl: timedelta,
        steps: int,
        intake: LivenessIntakePolicy,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._new_id = new_id
        self._ttl = ttl
        self._steps = steps
        self._intake = intake

    async def execute(
        self, *, tenant_id: UUID, external_subject_id: str, purpose: LivenessPurpose
    ) -> LivenessSessionView:
        # Não confere se o subject existe: a criação não vira oráculo de cadastro.
        challenge = LivenessChallenge.create(
            id=self._new_id(),
            tenant_id=tenant_id,
            external_subject_id=external_subject_id,
            purpose=purpose,
            steps=tuple(step.value for step in new_challenge(self._steps)),
            ttl=self._ttl,
            now=self._clock.now(),
        )
        async with self._uow_factory() as uow:
            await uow.liveness_challenges.add(challenge)
            await uow.commit()
        return LivenessSessionView(
            session_id=challenge.id,
            external_subject_id=external_subject_id,
            purpose=purpose.value,
            steps=challenge.steps,
            expires_at=challenge.expires_at,
            min_frames=self._intake.min_frames,
            max_frames=self._intake.max_frames,
            frame_max_bytes=self._intake.frame_max_bytes,
        )


def check_liveness_submission(
    submission: LivenessSubmission | None, intake: LivenessIntakePolicy
) -> None:
    """Validação barata, antes de tocar banco e storage."""
    if submission is None:
        if intake.required:
            raise LivenessEvidenceRequired(
                "este serviço exige prova de vida: envie liveness_session_id e os quadros "
                "(POST /api/v1/liveness-sessions)"
            )
        return
    if not intake.min_frames <= len(submission.frames) <= intake.max_frames:
        raise InvalidCapture(
            f"envie de {intake.min_frames} a {intake.max_frames} quadros de liveness"
        )
    frame_policy = CapturePolicy(max_bytes=intake.frame_max_bytes)
    for frame in submission.frames:
        validate_capture(frame, frame_policy)


async def consume_liveness(
    uow: UnitOfWork,
    storage: CaptureStorage,
    *,
    submission: LivenessSubmission,
    tenant_id: UUID,
    subject_id: UUID,
    external_subject_id: str,
    purpose: LivenessPurpose,
    by_id: UUID,
    clock: Clock,
) -> tuple[UUID, tuple[str, ...]]:
    """Marca a sessão como usada e guarda os quadros. Chamado dentro da transação."""
    challenge = await uow.liveness_challenges.get_for_tenant(
        tenant_id, submission.session_id, for_update=True
    )
    if challenge is None:
        raise LivenessSessionNotFound("sessão de liveness não encontrada")
    prefix = capture_key(tenant_id, subject_id, "liveness", challenge.id)
    keys = tuple(f"{prefix}/{n:03d}" for n in range(len(submission.frames)))
    challenge.consume(
        external_subject_id=external_subject_id,
        purpose=purpose,
        by_id=by_id,
        frame_keys=keys,
        now=clock.now(),
    )
    for key, frame in zip(keys, submission.frames, strict=True):
        await storage.put(key, frame)
    await uow.liveness_challenges.save(challenge)
    return challenge.id, keys


async def load_liveness_evidence(
    uow_factory: UnitOfWorkFactory,
    storage: CaptureStorage,
    challenge_id: UUID | None,
    capture: CaptureData,
) -> tuple[LivenessEvidence | None, tuple[str, ...]]:
    """Evidência para o LivenessProvider + chaves dos quadros (para a retenção).

    Sem desafio: evidência só com a captura (comportamento anterior). Falha no
    storage propaga CaptureStorageError (vira ERROR/CAPTURE_UNAVAILABLE).
    """
    if challenge_id is None:
        return None, ()
    async with uow_factory() as uow:
        challenge = await uow.liveness_challenges.get(challenge_id)
    if challenge is None:
        return None, ()
    frames = tuple([await storage.get(key) for key in challenge.frame_keys])
    evidence = LivenessEvidence(
        capture=capture,
        session_reference=str(challenge.id),
        frames=frames,
        challenge=challenge.steps,
    )
    return evidence, challenge.frame_keys


async def liveness_frame_keys(
    uow_factory: UnitOfWorkFactory, challenge_id: UUID | None
) -> tuple[str, ...]:
    """Chaves dos quadros a apagar junto com a captura (vale até sem análise, ex.: EXPIRED)."""
    if challenge_id is None:
        return ()
    async with uow_factory() as uow:
        challenge = await uow.liveness_challenges.get(challenge_id)
    return challenge.frame_keys if challenge else ()


async def observe_frame(
    detector: FaceDetector, capture: CaptureData
) -> tuple[FrameObservation, FaceDetectionResult]:
    """Mede UM quadro com a mesma regra da decisão (giro, distância entre olhos).

    Devolve também a detecção (dimensões e caixa), para a página desenhar a guia.

    Só para a captura guiada da página /dev/liveness (APP_ENV=local): o front avança
    o desafio quando a medida cumpre o passo. Quem decide continua sendo o worker,
    com todos os quadros.
    """
    detection = await detector.detect(capture)
    return observe(detection.faces), detection
