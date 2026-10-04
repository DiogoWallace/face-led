"""PROTÓTIPO (POC) de BiometricProvider com OpenCV YuNet + SFace + MiniFASNet.

Não é registrado em app/infrastructure/biometric e não é usado pela API/worker.
Serve para verificar o encaixe no contrato atual. Os limites de qualidade são
parâmetros obrigatórios, sem valores padrão, porque ainda não foram calibrados.
"""

import sys
import threading
from pathlib import Path

import cv2
import numpy as np

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "scripts"))

from engines import MiniFASNetLiveness, OpenCVEngine  # noqa: E402

from app.application.ports import BiometricProviderError  # noqa: E402
from app.domain.value_objects import (  # noqa: E402
    CaptureData,
    FaceDetectionResult,
    FaceEmbedding,
    LivenessResult,
    LivenessVerdict,
    QualityAssessment,
)


class OpenCVSFaceProviderPrototype:
    name = "opencv-sface-prototype"
    model_name = "opencv-sface"
    model_version = "2021dec"

    def __init__(self, *, min_face_px: int, min_sharpness: float, liveness_min_real: float) -> None:
        self._min_face_px = min_face_px
        self._min_sharpness = min_sharpness
        self._liveness_min_real = liveness_min_real
        # Instância por thread: YuNet compartilhado entre threads causou segfault no POC.
        self._local = threading.local()

    def _engines(self):
        if not hasattr(self._local, "engine"):
            self._local.engine = OpenCVEngine()
            self._local.liveness = MiniFASNetLiveness()
        return self._local.engine, self._local.liveness

    @staticmethod
    def _decode(capture: CaptureData) -> np.ndarray:
        img = cv2.imdecode(np.frombuffer(capture.content, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise BiometricProviderError("imagem não decodificável")
        return img

    def _single_face(self, img):
        # FaceDetectionResult do domínio não carrega landmarks: é preciso detectar de novo.
        faces = self._engines()[0].detect(img)
        if len(faces) != 1:
            raise BiometricProviderError("esperado exatamente 1 rosto")
        return faces[0]

    async def detect_face(self, capture: CaptureData) -> FaceDetectionResult:
        faces = self._engines()[0].detect(self._decode(capture))
        return FaceDetectionResult(
            face_count=len(faces), confidence=max((f.score for f in faces), default=None)
        )

    async def assess_quality(
        self, capture: CaptureData, detection: FaceDetectionResult
    ) -> QualityAssessment:
        img = self._decode(capture)
        face = self._single_face(img)
        x, y, w, h = face.box
        gray = cv2.cvtColor(img[max(0, y) : y + h, max(0, x) : x + w], cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        issues = []
        if min(w, h) < self._min_face_px:
            issues.append("FACE_TOO_SMALL")
        if sharpness < self._min_sharpness:
            issues.append("BLURRY")
        return QualityAssessment(acceptable=not issues, score=sharpness, issues=tuple(issues))

    async def check_liveness(self, capture: CaptureData) -> LivenessResult:
        img = self._decode(capture)
        face = self._single_face(img)
        out = self._engines()[1].predict(img, face)
        verdict = (
            LivenessVerdict.LIVE if out["real_score"] >= self._liveness_min_real else LivenessVerdict.SPOOF
        )
        return LivenessResult(verdict=verdict, score=out["real_score"])

    async def generate_embedding(
        self, capture: CaptureData, detection: FaceDetectionResult
    ) -> FaceEmbedding:
        img = self._decode(capture)
        vector = self._engines()[0].embed(img, self._single_face(img)).astype(np.float32)
        return FaceEmbedding(
            vector=vector.tobytes(), model_name=self.model_name, model_version=self.model_version
        )

    async def compare(self, probe: FaceEmbedding, reference: FaceEmbedding) -> float:
        a = np.frombuffer(probe.vector, np.float32).reshape(1, -1)
        b = np.frombuffer(reference.vector, np.float32).reshape(1, -1)
        return float(self._engines()[0].compare(a, b))
