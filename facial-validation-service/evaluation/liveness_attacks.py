"""Ataque simulado ao liveness ativo (ADR-010): "apresentação de slides" com fotos da vítima.

O atacante junta fotos reais da vítima em várias poses (o LFW tem dezenas para
pessoas públicas), ordena por giro para formar um movimento "contínuo",
simula "aproximar" com zoom progressivo e apresenta a sequência à câmera,
seguindo o desafio sorteado. A selfie é outra foto frontal da vítima.

Mede: em quantas pessoas o ataque é MONTÁVEL (há fotos para formar o giro sem
saltos) e em quantas ele PASSA no liveness. Também roda o controle negativo:
a mesma sequência com a selfie de OUTRA pessoa (deve dar SAME_PERSON_MISMATCH).

Não substitui APCER/BPCER com capturas reais: mede um ataque específico,
reproduzível, sem expor ninguém além do dataset público.
"""

import asyncio
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.domain.services import ActiveLivenessParameters, ChallengeStep, observe
from app.domain.value_objects import CaptureData, LivenessEvidence
from app.infrastructure.biometric.liveness import ActiveChallengeLivenessProvider
from app.infrastructure.biometric.opencv import OpenCVRuntime, YuNetFaceDetector

L, R, C = ChallengeStep.TURN_LEFT, ChallengeStep.TURN_RIGHT, ChallengeStep.MOVE_CLOSER


@dataclass(frozen=True)
class Shot:
    image: np.ndarray
    yaw: float
    eye_mid: tuple[float, float]
    eye_distance: float


def align(shot: Shot, target: Shot) -> np.ndarray:
    """Atacante cuidadoso: põe olhos na mesma escala e posição da foto frontal."""
    s = target.eye_distance / shot.eye_distance
    tx = target.eye_mid[0] - s * shot.eye_mid[0]
    ty = target.eye_mid[1] - s * shot.eye_mid[1]
    h, w = target.image.shape[:2]
    m = np.float32([[s, 0, tx], [0, s, ty]])
    return cv2.warpAffine(shot.image, m, (w, h), borderMode=cv2.BORDER_REPLICATE)


def jpeg(image: np.ndarray) -> CaptureData:
    return CaptureData(content=cv2.imencode(".jpg", image)[1].tobytes(), content_type="image/jpeg")


def zoom(image: np.ndarray, factor: float) -> np.ndarray:
    h, w = image.shape[:2]
    ch, cw = int(h / factor), int(w / factor)
    y, x = (h - ch) // 2, (w - cw) // 2
    return cv2.resize(image[y : y + ch, x : x + cw], (w, h), interpolation=cv2.INTER_LINEAR)


def ramp_to(
    shots: list[Shot], target: float, max_jump: float, frontal: Shot, aligned: bool
) -> list[np.ndarray] | None:
    """Fotos em ordem de giro, de ~0 até passar do alvo, sem saltos maiores que max_jump."""
    sign = 1 if target > 0 else -1
    pool = sorted((s for s in shots if s.yaw * sign >= 0), key=lambda s: abs(s.yaw))
    path, last = [], 0.0
    for shot in pool:
        if abs(shot.yaw) - last <= max_jump * 0.9 and abs(shot.yaw) > last:
            path.append(shot)
            last = abs(shot.yaw)
            if last >= abs(target):
                images = [align(s, frontal) if aligned else s.image for s in path]
                return images + images[::-1]
    return None


async def strong_ramp(
    detector, shots: list[Shot], front: Shot, target: float, p: ActiveLivenessParameters
) -> list[np.ndarray] | None:
    """Atacante forte: realinha, REMEDE cada foto e encadeia só passos dentro dos limites."""
    sign = 1 if target > 0 else -1
    measured = []
    for shot in shots:
        image = align(shot, front)
        obs = observe((await detector.detect(jpeg(image))).faces)
        if obs.tracked and obs.yaw * sign > 0:
            measured.append((abs(obs.yaw), obs.eye_distance, image))
    measured.sort(key=lambda m: m[0])
    base = observe((await detector.detect(jpeg(front.image))).faces)
    path, last_yaw, last_eyes = [], abs(base.yaw), base.eye_distance
    for yaw, eyes, image in measured:
        ok_pose = 0 < yaw - last_yaw <= p.max_yaw_jump * 0.9
        ok_scale = abs(eyes / last_eyes - 1) <= p.max_scale_jump * 0.9
        ok_flat = eyes / base.eye_distance >= p.turn_min_eye_ratio
        if ok_pose and ok_scale and ok_flat:
            path.append(image)
            last_yaw, last_eyes = yaw, eyes
            if yaw >= abs(target):
                return path + path[::-1]
    return None


async def build_strong_sequence(detector, shots, front: Shot, steps, p) -> list | None:
    frontal = front.image
    sequence = [frontal] * 3
    for step in steps:
        if step is C:
            factors = [1.0, 1.08, 1.16, 1.24, 1.32, 1.4]
            sequence += [zoom(frontal, f) for f in factors + factors[::-1]]
        else:
            margin = p.turn_min_yaw + 0.03
            path = await strong_ramp(detector, shots, front, margin if step is L else -margin, p)
            if path is None:
                return None
            sequence += path + [frontal]
    return sequence


def build_sequence(
    shots: list[Shot], front: Shot, steps, p: ActiveLivenessParameters, aligned: bool
) -> list[np.ndarray] | None:
    frontal = front.image
    sequence = [frontal] * 3
    margin = p.turn_min_yaw + 0.03
    for step in steps:
        if step is C:
            factors = [1.0, 1.08, 1.16, 1.24, 1.32, 1.4]
            sequence += [zoom(frontal, f) for f in factors + factors[::-1]]
        else:
            path = ramp_to(shots, margin if step is L else -margin, p.max_yaw_jump, front, aligned)
            if path is None:
                return None
            sequence += path + [frontal]
    return sequence


async def run(
    dataset_dir: Path,
    models_dir: Path,
    people: int = 60,
    seed: int = 3,
    aligned: bool = False,
    strong: bool = False,
) -> dict:
    p = ActiveLivenessParameters()
    runtime = OpenCVRuntime(
        models_dir=models_dir,
        inference_threads=1,
        opencv_threads=None,
        detector_score_threshold=0.6,
    )
    detector = YuNetFaceDetector(runtime)
    # Limiar de mesma pessoa só deste experimento: o EER do LFW (não é produção).
    provider = ActiveChallengeLivenessProvider(runtime, p, same_person_min_similarity=0.2778)
    await runtime.start()

    every = sorted((dataset_dir / "lfw").iterdir())
    folders = [d for d in every if len(list(d.glob("*.jpg"))) >= 30]
    rng = random.Random(seed)  # noqa: S311 - experimento reproduzível, não segurança
    rng.shuffle(folders)
    others = [d for d in every if len(list(d.glob("*.jpg"))) == 1]

    results, feasible, negative = Counter(), 0, Counter()
    challenges = [(L, R, C), (R, C, L), (L, R), (C, R, L)]
    for person in folders[:people]:
        shots, frontals = [], []
        for path in sorted(person.glob("*.jpg")):
            image = cv2.imread(str(path))
            faces = (await detector.detect(jpeg(image))).faces
            obs = observe(faces)
            if obs.tracked:
                lm = faces[0].landmarks
                mid = ((lm.right_eye.x + lm.left_eye.x) / 2, (lm.right_eye.y + lm.left_eye.y) / 2)
                shot = Shot(image, obs.yaw, mid, obs.eye_distance)
                shots.append(shot)
                if abs(obs.yaw) <= 0.05:
                    frontals.append(shot)
        if len(frontals) < 2:
            results["SEM_FOTO_FRONTAL"] += 1
            continue
        steps = challenges[len(results) % len(challenges)]
        sequence = (
            await build_strong_sequence(detector, shots, frontals[0], steps, p)
            if strong
            else build_sequence(shots, frontals[0], steps, p, aligned)
        )
        if sequence is None:
            results["NAO_MONTAVEL"] += 1
            continue
        feasible += 1
        evidence = LivenessEvidence(
            capture=jpeg(frontals[1].image),
            frames=tuple(jpeg(f) for f in sequence),
            challenge=tuple(s.value for s in steps),
        )
        outcome = await provider.check(evidence)
        results[f"{outcome.verdict.value}:{outcome.detail}"] += 1
        if outcome.verdict.value == "LIVE":
            stranger = cv2.imread(str(next(rng.choice(others).glob("*.jpg"))))
            control = await provider.check(
                LivenessEvidence(
                    capture=jpeg(stranger), frames=evidence.frames, challenge=evidence.challenge
                )
            )
            negative[f"{control.verdict.value}:{control.detail}"] += 1
    await runtime.close()
    passed = sum(v for k, v in results.items() if k.startswith("LIVE"))
    return {
        "attacker": "forte (alinha, remede, encadeia)"
        if strong
        else ("alinhado" if aligned else "ingênuo"),
        "people_tried": min(people, len(folders)),
        "attack_feasible": feasible,
        "attack_passed": passed,
        "outcomes": dict(results.most_common()),
        "negative_control_other_selfie": dict(negative.most_common()),
    }


if __name__ == "__main__":
    import json

    for kwargs in ({}, {"aligned": True}, {"strong": True}):
        result = asyncio.run(run(Path("datasets/lfw"), Path("models"), **kwargs))
        print(json.dumps(result, indent=2, ensure_ascii=False))
