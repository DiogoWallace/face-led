"""Medidor de qualidade OpenCV com imagens sintéticas (sem pessoas, sem modelo ONNX).

Prova que as medidas reagem no sentido certo (borrado < nítido, escuro < claro)
e que a decisão continua no QualityGate. Os LIMITES não são testados como
valores de produção: todos são PENDING CALIBRATION.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from app.domain.services import QualityGate, QualityMeasurementUnavailable, QualityRequirements
from app.domain.value_objects import (
    BoundingBox,
    CaptureData,
    DetectedFace,
    FaceDetectionResult,
    QualityIssue,
)
from app.infrastructure.biometric import build_biometric_setup, build_components
from app.infrastructure.biometric.opencv import OpenCVQualityAssessor, OpenCVRuntime
from app.infrastructure.biometric.opencv.quality import face_region, measure_face
from tests.unit.test_biometric_composition import settings

BOX = BoundingBox(40, 30, 160, 180)


def noise(width=240, height=240, seed=7) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)


def bright_noise() -> np.ndarray:
    return np.clip(noise().astype(int) // 8 + 230, 0, 255).astype(np.uint8)


def flat(value: int, width=240, height=240) -> np.ndarray:
    return np.full((height, width, 3), value, dtype=np.uint8)


def png(image: np.ndarray) -> CaptureData:
    return CaptureData(content=cv2.imencode(".png", image)[1].tobytes(), content_type="image/png")


class TestMeasurements:
    def test_blur_lowers_sharpness(self):
        sharp = noise()
        blurred = cv2.GaussianBlur(sharp, (0, 0), 3)
        assert measure_face(blurred, BOX).sharpness < measure_face(sharp, BOX).sharpness / 10

    def test_brightness_is_mean_gray_of_the_face_region(self):
        assert measure_face(flat(0), BOX).brightness == 0
        assert measure_face(flat(255), BOX).brightness == 255
        image = flat(0)
        image[30:210, 40:200] = 200  # só a região do rosto clara
        assert measure_face(image, BOX).brightness == pytest.approx(200)

    def test_flat_region_has_zero_sharpness(self):
        assert measure_face(flat(128), BOX).sharpness == 0

    @pytest.mark.parametrize("blur", [0, 2])
    def test_sharpness_is_comparable_across_face_sizes(self, blur):
        # Textura suave (ruído desfocado), ampliada de 112 a 900 px: o mesmo "rosto"
        # em tamanhos diferentes. Na resolução original a variância cai >20×; em
        # 112×112 tem de ficar estável.
        texture = cv2.GaussianBlur(noise(224, 224, seed=3), (0, 0), 2)
        if blur:
            texture = cv2.GaussianBlur(texture, (0, 0), blur)
        values = []
        for side in (112, 224, 448, 900):
            interpolation = cv2.INTER_AREA if side < 224 else cv2.INTER_CUBIC
            image = cv2.resize(texture, (side, side), interpolation=interpolation)
            values.append(measure_face(image, BoundingBox(0, 0, side, side)).sharpness)
        assert max(values) / min(values) < 1.15

    def test_box_is_clipped_to_the_image(self):
        region = face_region(noise(), BoundingBox(-20, 200, 100, 100))
        assert region.shape[:2] == (40, 80)

    def test_box_outside_the_image_yields_no_measurement(self):
        measured = measure_face(noise(), BoundingBox(500, 500, 50, 50))
        assert measured.sharpness is None and measured.brightness is None


class TestGateDecidesFromMeasurements:
    """Limites SOMENTE de teste — não são calibração."""

    REQUIREMENTS = QualityRequirements(
        min_face_px=50, min_sharpness=50, min_brightness=40, max_brightness=220
    )

    def evaluate(self, image: np.ndarray, box: BoundingBox = BOX):
        h, w = image.shape[:2]
        detection = FaceDetectionResult(
            faces=(DetectedFace(box=box),), image_width=w, image_height=h
        )
        return QualityGate(self.REQUIREMENTS).evaluate(
            detection, detection.faces[0], measure_face(image, box)
        )

    def test_sharp_and_well_lit_passes(self):
        image = flat(120)
        image[30:210, 40:200] = noise(160, 180) // 2 + 64  # textura, brilho médio
        report = self.evaluate(image)
        assert report.passed, report.issues

    @pytest.mark.parametrize(
        ("image", "issue"),
        [
            (cv2.GaussianBlur(flat(120), (0, 0), 5), QualityIssue.BLURRY),
            (noise() // 8, QualityIssue.TOO_DARK),
            (bright_noise(), QualityIssue.TOO_BRIGHT),
        ],
        ids=["blurry", "dark", "bright"],
    )
    def test_rejections_carry_issue_codes(self, image, issue):
        report = self.evaluate(image)
        assert not report.passed
        assert issue.value in report.issues
        assert report.sharpness is not None and report.brightness is not None

    def test_configured_criterion_without_measurement_is_error(self):
        with pytest.raises(QualityMeasurementUnavailable):
            self.evaluate(noise(), BoundingBox(500, 500, 60, 60))


class TestAssessorRuntime:
    async def test_runs_in_the_executor_without_model_files(self, tmp_path: Path):
        runtime = OpenCVRuntime(
            models_dir=tmp_path,  # vazio: o medidor não precisa de modelo
            inference_threads=1,
            opencv_threads=None,
            detector_score_threshold=0.6,
        )
        assessor = OpenCVQualityAssessor(runtime)
        await runtime.start()
        try:
            measured = await assessor.measure(png(flat(90)), DetectedFace(box=BOX))
        finally:
            await runtime.close()
        assert measured.brightness == 90
        assert assessor.name == "opencv-quality/v1"

    def test_catalog_key_and_shared_runtime(self):
        components = build_components(
            settings(biometric_detector="opencv-yunet", biometric_quality_assessor="opencv")
        )
        assert isinstance(components.quality_assessor, OpenCVQualityAssessor)
        assert components.quality_assessor._runtime is components.detector._runtime

    def test_quality_only_setup_requires_no_model(self, tmp_path):
        setup = build_biometric_setup(
            settings(biometric_quality_assessor="opencv", biometric_models_dir=str(tmp_path))
        )
        assert setup.loaded_models == ()
        assert len(setup.runtimes) == 1
