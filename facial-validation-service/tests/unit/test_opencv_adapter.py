"""Adapter OpenCV sem os modelos: formatos, comparator, ciclo de vida e composição.

Nada aqui reconhece rosto. Os testes com os modelos ONNX reais estão em
tests/models/ (marcador `models`).
"""

import hashlib
from pathlib import Path

import numpy as np
import pytest

from app.application.ports import BiometricProviderError
from app.domain.value_objects import (
    BoundingBox,
    CaptureData,
    DetectedFace,
    FaceEmbedding,
    Point,
)
from app.infrastructure.biometric import build_biometric_setup, build_components
from app.infrastructure.biometric.opencv import (
    SFACE,
    YUNET,
    ModelFile,
    ModelIntegrityError,
    OpenCVRuntime,
    SFaceCosineComparator,
    SFaceEmbedder,
    YuNetFaceDetector,
    verify_model,
)
from app.infrastructure.biometric.opencv.models import download_model
from app.infrastructure.biometric.opencv.runtime import decode_image
from app.infrastructure.biometric.opencv.sface import (
    SFACE_MODEL_NAME,
    SFACE_MODEL_VERSION,
    vector_from_bytes,
    vector_to_bytes,
)
from app.infrastructure.biometric.opencv.yunet_format import (
    face_from_yunet_row,
    yunet_row_from_face,
)
from tests.fakes import JPEG
from tests.unit.test_biometric_composition import settings

ROW = [10, 20, 100, 120, 40, 60, 80, 61, 60, 85, 45, 105, 75, 106, 0.93]


def runtime(tmp_path: Path, threads: int = 1) -> OpenCVRuntime:
    return OpenCVRuntime(
        models_dir=tmp_path,
        inference_threads=threads,
        opencv_threads=None,
        detector_score_threshold=0.6,
    )


def sface(vector) -> FaceEmbedding:
    return FaceEmbedding(
        vector=vector_to_bytes(np.asarray(vector, dtype=np.float32)),
        model_name=SFACE_MODEL_NAME,
        model_version=SFACE_MODEL_VERSION,
    )


class TestYuNetFormat:
    def test_row_maps_box_confidence_and_landmarks(self):
        face = face_from_yunet_row(ROW)
        assert face.box == BoundingBox(10, 20, 100, 120)
        assert face.confidence == pytest.approx(0.93)
        assert face.landmarks.right_eye == Point(40, 60)
        assert face.landmarks.mouth_left == Point(75, 106)

    def test_round_trip_preserves_the_native_row(self):
        row = yunet_row_from_face(face_from_yunet_row(ROW))
        assert row.dtype == np.float32
        np.testing.assert_allclose(row, np.array(ROW, dtype=np.float32))

    def test_unexpected_row_size(self):
        with pytest.raises(BiometricProviderError):
            face_from_yunet_row(ROW[:-1])

    def test_alignment_requires_landmarks(self):
        with pytest.raises(BiometricProviderError):
            yunet_row_from_face(DetectedFace(box=BoundingBox(0, 0, 10, 10)))


class TestTemplateFormat:
    def test_128_float32_little_endian(self):
        data = vector_to_bytes(np.arange(128, dtype=np.float32))
        assert len(data) == 512
        np.testing.assert_array_equal(vector_from_bytes(data), np.arange(128))

    @pytest.mark.parametrize("size", [127, 129, 512])
    def test_rejects_other_dimensions(self, size):
        with pytest.raises(BiometricProviderError):
            vector_to_bytes(np.zeros(size, dtype=np.float32))

    def test_rejects_truncated_template(self):
        with pytest.raises(BiometricProviderError):
            vector_from_bytes(b"\x00" * 511)


class TestSFaceCosineComparator:
    async def test_cosine_similarity(self):
        comparator = SFaceCosineComparator()
        a = np.zeros(128)
        a[0] = 3.0
        b = np.zeros(128)
        b[1] = 2.0
        assert await comparator.similarity(sface(a), sface(a * 7)) == pytest.approx(1.0)
        assert await comparator.similarity(sface(a), sface(b)) == pytest.approx(0.0)
        assert await comparator.similarity(sface(a), sface(-a)) == pytest.approx(-1.0)

    async def test_refuses_other_models(self):
        other = FaceEmbedding(vector=b"\x00" * 512, model_name="dlib", model_version="1")
        with pytest.raises(BiometricProviderError):
            await SFaceCosineComparator().similarity(sface(np.ones(128)), other)

    async def test_refuses_zero_vector(self):
        with pytest.raises(BiometricProviderError):
            await SFaceCosineComparator().similarity(sface(np.ones(128)), sface(np.zeros(128)))


class TestRuntimeLifecycle:
    async def test_adapter_without_started_runtime_fails_as_provider_error(self, tmp_path):
        detector = YuNetFaceDetector(runtime(tmp_path))
        with pytest.raises(BiometricProviderError):
            await detector.detect(CaptureData(content=JPEG, content_type="image/jpeg"))

    async def test_embedder_without_landmarks_fails_before_inference(self, tmp_path):
        embedder = SFaceEmbedder(runtime(tmp_path))
        with pytest.raises(BiometricProviderError):
            await embedder.embed(
                CaptureData(content=JPEG, content_type="image/jpeg"),
                DetectedFace(box=BoundingBox(0, 0, 10, 10)),
            )

    async def test_start_refuses_missing_models(self, tmp_path):
        rt = runtime(tmp_path)
        YuNetFaceDetector(rt)
        with pytest.raises(ModelIntegrityError, match="ausente"):
            await rt.start()
        assert not rt.started

    async def test_start_refuses_tampered_model(self, tmp_path):
        (tmp_path / YUNET.filename).write_bytes(b"not the model")
        rt = runtime(tmp_path)
        YuNetFaceDetector(rt)
        with pytest.raises(ModelIntegrityError, match="SHA-256"):
            await rt.start()

    async def test_start_without_opencv_components_is_noop(self, tmp_path):
        rt = runtime(tmp_path)
        await rt.start()
        assert not rt.started

    def test_models_must_be_declared_before_start(self, tmp_path):
        rt = runtime(tmp_path)
        rt._executor = object()  # simula runtime iniciado
        with pytest.raises(RuntimeError):
            rt.require(SFACE)

    def test_invalid_thread_count(self, tmp_path):
        with pytest.raises(ValueError):
            runtime(tmp_path, threads=0)

    def test_undecodable_image(self):
        with pytest.raises(BiometricProviderError):
            decode_image(JPEG)  # assinatura JPEG válida, conteúdo não é imagem


class TestModelFiles:
    def test_verify_accepts_matching_hash(self, tmp_path):
        content = b"modelo de teste"
        model = ModelFile(
            filename="m.onnx",
            url="https://example.invalid/m.onnx",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            license="-",
        )
        (tmp_path / "m.onnx").write_bytes(content)
        assert verify_model(tmp_path, model) == tmp_path / "m.onnx"
        # Já presente e válido: não toca a rede.
        assert download_model(tmp_path, model) is False

    def test_download_requires_https(self, tmp_path):
        model = ModelFile("m.onnx", "http://example.invalid/m.onnx", "0" * 64, 1, "-")
        with pytest.raises(ModelIntegrityError):
            download_model(tmp_path, model)


class TestComposition:
    OPENCV = dict(
        biometric_detector="opencv-yunet",
        biometric_embedder="opencv-sface",
        biometric_comparator="opencv-sface",
    )

    def test_opencv_components_share_one_runtime(self):
        components = build_components(settings(**self.OPENCV))
        assert isinstance(components.detector, YuNetFaceDetector)
        assert isinstance(components.embedder, SFaceEmbedder)
        assert isinstance(components.comparator, SFaceCosineComparator)
        assert components.detector._runtime is components.embedder._runtime
        assert components.describe() == {
            "detector": "opencv-yunet/2023mar",
            "quality_assessor": "none",
            "embedder": "opencv-sface/2021dec",
            "comparator": "opencv-sface-cosine",
            "liveness": "none",
        }

    def test_building_does_not_load_models(self, tmp_path):
        # A API monta o mesmo setup e nunca chama start(): nada é lido do disco.
        setup = build_biometric_setup(
            settings(**self.OPENCV, biometric_models_dir=str(tmp_path / "absent"))
        )
        assert len(setup.runtimes) == 1
        assert not setup.runtimes[0].started
        assert setup.loaded_models == (YUNET.filename, SFACE.filename)

    async def test_start_fails_fast_without_models(self, tmp_path):
        setup = build_biometric_setup(
            settings(**self.OPENCV, biometric_models_dir=str(tmp_path / "absent"))
        )
        with pytest.raises(ModelIntegrityError):
            await setup.start()

    def test_default_setup_has_no_runtime(self):
        assert build_biometric_setup(settings()).runtimes == ()
