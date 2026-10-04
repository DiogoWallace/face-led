"""Liveness ativo próprio: o desafio sorteado foi cumprido, ao vivo e com movimento contínuo?

Decisão do usuário (ADR-010): prova de vida feita pelo próprio serviço, sem
fornecedor. O servidor sorteia passos (virar à esquerda, à direita, aproximar),
o front captura quadros enquanto a pessoa executa, e esta regra decide a
partir das MEDIDAS por quadro (giro, distância entre olhos) — sem OpenCV.

O que protege: foto impressa ou em tela parada (não gira), foto plana
inclinada (giro pequeno e olhos "encolhendo" demais), troca de fotos (salto de
pose entre quadros) e vídeo gravado (ordem sorteada por sessão).
O que NÃO protege: injeção digital (câmera virtual, deepfake em tempo real) —
exige atestação do dispositivo. Sem certificação; APCER/BPCER não medidos.

Convenção dos quadros: SEM espelhamento (como a câmera vê). "Esquerda" é a
esquerda da PESSOA, que na imagem fica à direita: virar para a esquerda leva o
nariz para x maior.
"""

import math
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.exceptions import InvalidPolicy
from app.domain.value_objects.biometric import DetectedFace, LivenessVerdict


class ChallengeStep(StrEnum):
    TURN_LEFT = "TURN_LEFT"
    TURN_RIGHT = "TURN_RIGHT"
    MOVE_CLOSER = "MOVE_CLOSER"


def new_challenge(
    length: int, rng: secrets.SystemRandom | None = None
) -> tuple[ChallengeStep, ...]:
    """Sequência sorteada pelo servidor: sem passo repetido em seguida, aproximar no máx. 1×."""
    if not 2 <= length <= 4:
        raise InvalidPolicy("o desafio deve ter de 2 a 4 passos")
    rng = rng or secrets.SystemRandom()
    while True:
        steps: list[ChallengeStep] = []
        for _ in range(length):
            options = [
                s
                for s in ChallengeStep
                if (not steps or s is not steps[-1])
                and not (s is ChallengeStep.MOVE_CLOSER and ChallengeStep.MOVE_CLOSER in steps)
            ]
            steps.append(rng.choice(options))
        if ChallengeStep.TURN_LEFT in steps or ChallengeStep.TURN_RIGHT in steps:
            return tuple(steps)


@dataclass(frozen=True, slots=True)
class FrameObservation:
    """Medidas de um quadro. `yaw`/`eye_distance` só existem com exatamente 1 rosto."""

    face_count: int
    yaw: float | None = None
    eye_distance: float | None = None

    @property
    def tracked(self) -> bool:
        return self.face_count == 1 and self.yaw is not None and self.eye_distance is not None


def observe(faces: Sequence[DetectedFace]) -> FrameObservation:
    """Giro = deslocamento do nariz em relação ao ponto médio dos olhos, na escala dos olhos."""
    if len(faces) != 1 or faces[0].landmarks is None:
        return FrameObservation(face_count=len(faces))
    lm = faces[0].landmarks
    mid_x = (lm.right_eye.x + lm.left_eye.x) / 2
    eye_distance = math.dist((lm.right_eye.x, lm.right_eye.y), (lm.left_eye.x, lm.left_eye.y))
    if eye_distance <= 0:
        return FrameObservation(face_count=1)
    return FrameObservation(
        face_count=1, yaw=(lm.nose_tip.x - mid_x) / eye_distance, eye_distance=eye_distance
    )


@dataclass(frozen=True, slots=True)
class ActiveLivenessParameters:
    """Valores INICIAIS, PENDING CALIBRATION: não medidos com capturas reais (ADR-010)."""

    min_frames: int = 8
    min_tracked_ratio: float = 0.8
    frontal_max_yaw: float = 0.15
    turn_min_yaw: float = 0.35
    # Foto plana inclinada só atinge o giro com os olhos encolhendo ~50%; giro real, pouco.
    turn_min_eye_ratio: float = 0.65
    closer_min_scale: float = 1.25
    # Continuidade: um giro real passa pelas poses intermediárias; troca de foto salta.
    max_yaw_jump: float = 0.25
    max_scale_jump: float = 0.25
    max_untracked_gap: int = 1

    def __post_init__(self) -> None:
        if not (0 < self.frontal_max_yaw < self.turn_min_yaw):
            raise InvalidPolicy("frontal_max_yaw deve ser > 0 e menor que turn_min_yaw")
        if not (0 < self.min_tracked_ratio <= 1 and 0 < self.turn_min_eye_ratio <= 1):
            raise InvalidPolicy("razões de liveness devem estar em (0, 1]")
        if self.closer_min_scale <= 1 or self.min_frames < 2:
            raise InvalidPolicy("parâmetros de liveness inválidos")


@dataclass(frozen=True, slots=True)
class ChallengeOutcome:
    verdict: LivenessVerdict
    detail: str
    # Quadros que provam o desafio (início frontal + um por passo): é neles que o
    # provider confere se é a MESMA pessoa da selfie.
    checkpoints: tuple[int, ...] = ()


def verify_challenge(
    observations: Sequence[FrameObservation],
    steps: Sequence[ChallengeStep],
    p: ActiveLivenessParameters,
) -> ChallengeOutcome:
    if len(observations) < p.min_frames:
        return ChallengeOutcome(LivenessVerdict.INCONCLUSIVE, "TOO_FEW_FRAMES")
    tracked = [o.tracked for o in observations]
    if sum(tracked) / len(observations) < p.min_tracked_ratio:
        return ChallengeOutcome(LivenessVerdict.INCONCLUSIVE, "FACE_NOT_TRACKED")

    start = next(
        (i for i, o in enumerate(observations) if o.tracked and abs(o.yaw) <= p.frontal_max_yaw),
        None,
    )
    if start is None:
        return ChallengeOutcome(LivenessVerdict.SPOOF, "NO_FRONTAL_START")
    base = observations[start].eye_distance

    checkpoints = [start]
    cursor = start
    for n, step in enumerate(steps, start=1):
        found = next(
            (
                i
                for i in range(cursor + 1, len(observations))
                if observations[i].tracked and _satisfies(observations[i], step, base, p)
            ),
            None,
        )
        if found is None:
            return ChallengeOutcome(LivenessVerdict.SPOOF, f"STEP_{n}_{step.value}_NOT_DONE")
        checkpoints.append(found)
        cursor = found

    broken = _continuity_break(observations[start : cursor + 1], p)
    if broken:
        return ChallengeOutcome(LivenessVerdict.SPOOF, broken)
    return ChallengeOutcome(LivenessVerdict.LIVE, "CHALLENGE_COMPLETED", tuple(checkpoints))


def _satisfies(
    o: FrameObservation, step: ChallengeStep, base: float, p: ActiveLivenessParameters
) -> bool:
    ratio = o.eye_distance / base
    if step is ChallengeStep.MOVE_CLOSER:
        return ratio >= p.closer_min_scale and abs(o.yaw) <= p.turn_min_yaw
    if ratio < p.turn_min_eye_ratio:
        return False  # olhos "encolheram" demais: assinatura de foto plana inclinada
    return o.yaw >= p.turn_min_yaw if step is ChallengeStep.TURN_LEFT else o.yaw <= -p.turn_min_yaw


def _continuity_break(span: Sequence[FrameObservation], p: ActiveLivenessParameters) -> str | None:
    previous: FrameObservation | None = None
    gap = 0
    for o in span:
        if not o.tracked:
            gap += 1
            if gap > p.max_untracked_gap:
                return "TRACKING_LOST"
            continue
        if previous is not None:
            if abs(o.yaw - previous.yaw) > p.max_yaw_jump * (gap + 1):
                return "POSE_JUMP"
            if abs(o.eye_distance / previous.eye_distance - 1) > p.max_scale_jump * (gap + 1):
                return "SCALE_JUMP"
        previous, gap = o, 0
    return None
