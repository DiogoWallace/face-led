"""QualityGate: decisões puras sobre medições. Valores aqui são de teste, não calibração."""

import pytest

from app.domain.exceptions import InvalidPolicy
from app.domain.services import QualityGate, QualityMeasurementUnavailable, QualityRequirements
from app.domain.value_objects import (
    FaceDetectionResult,
    QualityIssue,
    QualityMeasurements,
    RejectionReason,
)
from tests.fakes import scripted_face


def detection(*sizes: float, image=(640, 480)) -> FaceDetectionResult:
    faces = tuple(scripted_face(px, offset=i * 200) for i, px in enumerate(sizes))
    return FaceDetectionResult(faces=faces, image_width=image[0], image_height=image[1])


GOOD = QualityMeasurements(sharpness=100.0, brightness=120.0)


class TestRequirements:
    def test_all_pending_by_default(self):
        assert QualityRequirements().pending() == (
            "min_face_px",
            "min_face_ratio",
            "min_sharpness",
            "min_brightness",
            "max_brightness",
        )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"min_face_px": -1},
            {"min_sharpness": float("nan")},
            {"min_face_ratio": 1.5},
            {"min_brightness": 200, "max_brightness": 100},
            {"min_scores": {"fiqa": float("inf")}},
        ],
    )
    def test_invalid_values(self, kwargs):
        with pytest.raises(InvalidPolicy):
            QualityRequirements(**kwargs)


class TestFaceCount:
    def test_single_face_passes(self):
        assert QualityGate(QualityRequirements()).face_count_rejection(detection(100)) is None

    def test_no_face(self):
        reason, report = QualityGate(QualityRequirements()).face_count_rejection(detection())
        assert reason is RejectionReason.NO_FACE
        assert report.issues == (QualityIssue.NO_FACE,)

    def test_multiple_faces_without_min_size(self):
        reason, _ = QualityGate(QualityRequirements()).face_count_rejection(detection(100, 20))
        assert reason is RejectionReason.MULTIPLE_FACES

    def test_background_face_ignored_with_min_size(self):
        gate = QualityGate(QualityRequirements(min_face_px=40))
        assert gate.face_count_rejection(detection(100, 20)) is None
        assert gate.considered_faces(detection(100, 20))[0].box.width == 100

    def test_only_small_faces_is_low_quality(self):
        gate = QualityGate(QualityRequirements(min_face_px=40))
        reason, report = gate.face_count_rejection(detection(20))
        assert reason is RejectionReason.LOW_QUALITY
        assert report.issues == (QualityIssue.FACE_TOO_SMALL,)


class TestEvaluate:
    def evaluate(self, requirements, measurements=GOOD, size=100.0, image=(640, 480)):
        det = detection(size, image=image)
        return QualityGate(requirements).evaluate(det, det.faces[0], measurements)

    def test_no_requirements_passes_and_reports_measures(self):
        report = self.evaluate(QualityRequirements())
        assert report.passed and report.issues == ()
        assert report.face_min_side_px == 100
        assert report.face_ratio == round(100 * 100 / (640 * 480), 4)

    @pytest.mark.parametrize(
        ("requirements", "measurements", "issue"),
        [
            (QualityRequirements(min_face_px=150), GOOD, QualityIssue.FACE_TOO_SMALL),
            (QualityRequirements(min_face_ratio=0.5), GOOD, QualityIssue.FACE_TOO_SMALL_RELATIVE),
            (QualityRequirements(min_sharpness=200), GOOD, QualityIssue.BLURRY),
            (QualityRequirements(min_brightness=130), GOOD, QualityIssue.TOO_DARK),
            (QualityRequirements(max_brightness=110), GOOD, QualityIssue.TOO_BRIGHT),
        ],
    )
    def test_single_criterion_failures(self, requirements, measurements, issue):
        report = self.evaluate(requirements, measurements)
        assert not report.passed
        assert report.issues == (issue,)

    def test_multiple_issues_are_all_reported(self):
        report = self.evaluate(
            QualityRequirements(min_face_px=150, min_sharpness=200, min_brightness=130)
        )
        assert set(report.issues) == {
            QualityIssue.FACE_TOO_SMALL,
            QualityIssue.BLURRY,
            QualityIssue.TOO_DARK,
        }

    def test_named_score_requirement(self):
        measurements = QualityMeasurements(sharpness=100, brightness=120, scores={"fiqa": 0.2})
        report = self.evaluate(QualityRequirements(min_scores={"fiqa": 0.5}), measurements)
        assert report.issues == ("SCORE_BELOW_MINIMUM:fiqa",)

    @pytest.mark.parametrize(
        ("requirements", "measurements"),
        [
            (QualityRequirements(min_sharpness=10), QualityMeasurements(brightness=100)),
            (QualityRequirements(max_brightness=10), QualityMeasurements(sharpness=100)),
            (QualityRequirements(min_scores={"fiqa": 0.1}), GOOD),
        ],
    )
    def test_configured_criterion_without_measurement_never_passes(
        self, requirements, measurements
    ):
        with pytest.raises(QualityMeasurementUnavailable):
            self.evaluate(requirements, measurements)

    def test_audit_data_has_no_biometric_payload(self):
        data = self.evaluate(QualityRequirements(min_sharpness=10)).as_audit_data()
        assert set(data) == {
            "passed",
            "issues",
            "face_min_side_px",
            "face_ratio",
            "sharpness",
            "brightness",
        }
