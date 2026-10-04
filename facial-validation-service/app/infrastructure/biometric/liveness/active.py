"""LivenessProvider ativo próprio (ADR-010): desafio sorteado + mesma pessoa da selfie.

1. YuNet em cada quadro → giro e distância entre olhos (`observe`);
2. regra de domínio `verify_challenge`: passos na ordem, início frontal,
   movimento contínuo, sem assinatura de foto plana;
3. SFace: cada quadro-prova (início + um por passo) precisa ser a MESMA pessoa
   da selfie enviada, acima de LIVENESS_SAME_PERSON_MIN_SIMILARITY. Sem isso,
   alguém passaria no desafio com o próprio rosto e cadastraria a foto de outro.

Usa o mesmo OpenCVRuntime do worker (instância por thread, fora do event loop).
"""

import asyncio

from app.application.ports import BiometricProviderError
from app.domain.services import (
    ActiveLivenessParameters,
    ChallengeStep,
    observe,
    verify_challenge,
)
from app.domain.value_objects import LivenessEvidence, LivenessResult, LivenessVerdict
from app.infrastructure.biometric.opencv import (
    OpenCVRuntime,
    SFaceCosineComparator,
    SFaceEmbedder,
    YuNetFaceDetector,
)


class ActiveChallengeLivenessProvider:
    name = "active-challenge/v1"

    def __init__(
        self,
        runtime: OpenCVRuntime,
        parameters: ActiveLivenessParameters,
        same_person_min_similarity: float | None,
    ) -> None:
        self._detector = YuNetFaceDetector(runtime)
        self._embedder = SFaceEmbedder(runtime)
        self._comparator = SFaceCosineComparator()
        self._parameters = parameters
        self._same_person_min = same_person_min_similarity

    async def check(self, evidence: LivenessEvidence) -> LivenessResult:
        if not evidence.frames or not evidence.challenge or evidence.capture is None:
            return _inconclusive("NO_CHALLENGE_EVIDENCE")
        if self._same_person_min is None:
            return _inconclusive("SAME_PERSON_THRESHOLD_NOT_CONFIGURED")
        try:
            steps = tuple(ChallengeStep(s) for s in evidence.challenge)
        except ValueError:
            return _inconclusive("UNKNOWN_CHALLENGE_STEP")

        detections = await asyncio.gather(*(self._detector.detect(f) for f in evidence.frames))
        outcome = verify_challenge([observe(d.faces) for d in detections], steps, self._parameters)
        if outcome.verdict is not LivenessVerdict.LIVE:
            return LivenessResult(verdict=outcome.verdict, detail=outcome.detail)

        selfie = await self._detector.detect(evidence.capture)
        if selfie.face_count != 1:
            return _inconclusive("SELFIE_FACE_COUNT")
        try:
            probe = await self._embedder.embed(evidence.capture, selfie.faces[0])
            similarities = []
            for index in outcome.checkpoints:
                frame_embedding = await self._embedder.embed(
                    evidence.frames[index], detections[index].faces[0]
                )
                similarities.append(await self._comparator.similarity(frame_embedding, probe))
        except BiometricProviderError:
            return _inconclusive("EMBEDDING_FAILED")

        lowest = round(min(similarities), 4)
        if lowest < self._same_person_min:
            return LivenessResult(
                verdict=LivenessVerdict.SPOOF, score=lowest, detail="SAME_PERSON_MISMATCH"
            )
        return LivenessResult(verdict=LivenessVerdict.LIVE, score=lowest, detail=outcome.detail)


def _inconclusive(detail: str) -> LivenessResult:
    return LivenessResult(verdict=LivenessVerdict.INCONCLUSIVE, detail=detail)
