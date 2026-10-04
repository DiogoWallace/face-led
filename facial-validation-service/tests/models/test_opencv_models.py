"""Adapter OpenCV com os modelos ONNX REAIS (YuNet 2023mar, SFace 2021dec).

Usa só imagens sintéticas, sem pessoas: nenhuma foto é versionada (ADR-004).
Cobre o que dá para provar sem rosto — encaixe no pipeline, formato do
template, paridade com o OpenCV e concorrência sem segfault. Taxas de acerto
(FAR/FRR) são da suíte de avaliação (Fase 4), com dataset externo.

Requer: python -m app.cli download-models   (SKIP se os arquivos não existirem)
"""

import asyncio
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.application.ports import FaceAnalysisComponents
from app.application.use_cases.capture_analysis import FaceAnalysisPipeline
from app.domain.services import LivenessRequirement, QualityGate, QualityRequirements
from app.domain.value_objects import (
    BoundingBox,
    CaptureData,
    DetectedFace,
    FaceLandmarks,
    Point,
    RejectionReason,
)
from app.infrastructure.biometric.opencv import (
    ALL_MODELS,
    SFACE,
    OpenCVQualityAssessor,
    OpenCVRuntime,
    SFaceCosineComparator,
    SFaceEmbedder,
    YuNetFaceDetector,
)
from app.infrastructure.biometric.opencv.sface import vector_from_bytes
from app.infrastructure.biometric.unconfigured import UnconfiguredLivenessProvider

MODELS_DIR = Path(os.environ.get("BIOMETRIC_MODELS_DIR", "models"))

pytestmark = [
    pytest.mark.models,
    pytest.mark.skipif(
        not all((MODELS_DIR / m.filename).is_file() for m in ALL_MODELS),
        reason="modelos ausentes (python -m app.cli download-models)",
    ),
]


def encode(image: np.ndarray, ext: str = ".jpg") -> CaptureData:
    ok, buffer = cv2.imencode(ext, image)
    assert ok
    content_type = "image/jpeg" if ext == ".jpg" else "image/png"
    return CaptureData(content=buffer.tobytes(), content_type=content_type)


def noise(width: int, height: int, seed: int = 7) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)


def gradient(width: int, height: int) -> np.ndarray:
    row = np.linspace(0, 255, width, dtype=np.uint8)
    return np.repeat(np.repeat(row[None, :, None], height, axis=0), 3, axis=2)


# Rosto "geométrico" plausível para exercitar o alinhamento do SFace (não é uma pessoa).
SYNTHETIC_FACE = DetectedFace(
    box=BoundingBox(60, 50, 120, 140),
    confidence=0.9,
    landmarks=FaceLandmarks(
        right_eye=Point(95, 100),
        left_eye=Point(145, 100),
        nose_tip=Point(120, 130),
        mouth_right=Point(100, 160),
        mouth_left=Point(140, 160),
    ),
)


@pytest.fixture
async def runtime():
    rt = OpenCVRuntime(
        models_dir=MODELS_DIR,
        inference_threads=2,
        opencv_threads=None,
        detector_score_threshold=0.6,
    )
    detector, embedder = YuNetFaceDetector(rt), SFaceEmbedder(rt)
    quality = OpenCVQualityAssessor(rt)
    await rt.start()
    yield rt, detector, embedder, quality
    await rt.close()


class TestDetector:
    @pytest.mark.parametrize(
        ("image", "ext"),
        [(noise(640, 480), ".jpg"), (gradient(320, 240), ".png"), (noise(1280, 720), ".png")],
    )
    async def test_no_face_in_synthetic_images(self, runtime, image, ext):
        _, detector, _, _ = runtime
        result = await detector.detect(encode(image, ext))
        assert result.face_count == 0
        assert (result.image_width, result.image_height) == (image.shape[1], image.shape[0])

    async def test_pipeline_rejects_with_no_face(self, runtime):
        _, detector, embedder, quality = runtime
        pipeline = FaceAnalysisPipeline(
            components=FaceAnalysisComponents(
                detector=detector,
                quality_assessor=quality,
                embedder=embedder,
                comparator=SFaceCosineComparator(),
                liveness=UnconfiguredLivenessProvider(),
            ),
            quality_gate=QualityGate(QualityRequirements()),
            liveness_requirement=LivenessRequirement.DISABLED_FOR_EVALUATION,
        )
        analysis = await pipeline.analyze(encode(noise(640, 480)))
        assert analysis.rejection is RejectionReason.NO_FACE
        assert analysis.embedding is None


class TestEmbedderAndComparator:
    async def test_template_format_and_determinism(self, runtime):
        _, _, embedder, _ = runtime
        capture = encode(noise(240, 240))
        first = await embedder.embed(capture, SYNTHETIC_FACE)
        second = await embedder.embed(capture, SYNTHETIC_FACE)
        assert (first.model_name, first.model_version) == ("opencv-sface", "2021dec")
        assert len(first.vector) == 512
        assert first.vector == second.vector
        assert await SFaceCosineComparator().similarity(first, second) == pytest.approx(1.0)

    async def test_cosine_matches_opencv_fr_cosine(self, runtime):
        _, _, embedder, _ = runtime
        a = await embedder.embed(encode(noise(240, 240, seed=1)), SYNTHETIC_FACE)
        b = await embedder.embed(encode(gradient(240, 240)), SYNTHETIC_FACE)
        ours = await SFaceCosineComparator().similarity(a, b)

        recognizer = cv2.FaceRecognizerSF.create(str(MODELS_DIR / SFACE.filename), "")
        theirs = recognizer.match(
            vector_from_bytes(a.vector).astype(np.float32).reshape(1, -1),
            vector_from_bytes(b.vector).astype(np.float32).reshape(1, -1),
            cv2.FaceRecognizerSF_FR_COSINE,
        )
        assert ours == pytest.approx(theirs, abs=1e-5)


class TestConcurrency:
    async def test_concurrent_inference_without_crash(self, runtime):
        """O POC derrubou o processo (segfault) com uma instância compartilhada.

        Aqui são 2 threads com instâncias próprias, tamanhos alternados (o que
        muda o setInputSize a cada chamada) e detector + embedder misturados.
        Um segfault encerraria o pytest inteiro. O medidor de qualidade usa o mesmo executor.
        """
        _, detector, embedder, quality = runtime
        small, large = encode(noise(320, 240, seed=3)), encode(noise(800, 600, seed=4))
        face_capture = encode(noise(240, 240, seed=5))

        detections = [detector.detect(small if i % 2 else large) for i in range(200)]
        embeddings = [embedder.embed(face_capture, SYNTHETIC_FACE) for _ in range(40)]
        measures = [quality.measure(face_capture, SYNTHETIC_FACE) for _ in range(40)]
        results = await asyncio.gather(*detections, *embeddings, *measures)

        found = results[:200]
        assert all(r.face_count == 0 for r in found)
        assert {(r.image_width, r.image_height) for r in found} == {(320, 240), (800, 600)}
        assert len({e.vector for e in results[200:240]}) == 1  # mesma entrada, mesmo template
        assert all(m == results[240] for m in results[240:])  # medições determinísticas
