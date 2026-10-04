"""Quality gate: decisão de qualidade antes de liveness, embedding e comparação.

Os valores de `QualityRequirements` vêm de configuração. Nenhum tem padrão: um
critério não configurado não é aplicado e aparece em `pending()` como
PENDING CALIBRATION. As medições vêm do FaceQualityAssessor (infraestrutura);
este módulo só decide.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from app.domain.exceptions import DomainError, InvalidPolicy
from app.domain.value_objects.biometric import (
    DetectedFace,
    FaceDetectionResult,
    QualityIssue,
    QualityMeasurements,
    QualityReport,
)
from app.domain.value_objects.status import RejectionReason

_CORE_FIELDS = (
    "min_face_px",
    "min_face_ratio",
    "min_sharpness",
    "min_brightness",
    "max_brightness",
)


class QualityMeasurementUnavailable(DomainError):
    """Critério configurado sem a medição correspondente: não aprovar em silêncio."""

    code = "QUALITY_MEASUREMENT_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class QualityRequirements:
    min_face_px: float | None = None
    min_face_ratio: float | None = None
    min_sharpness: float | None = None
    min_brightness: float | None = None
    max_brightness: float | None = None
    # Medidores nomeados futuros (ex.: {"fiqa": 0.4}). Valores PENDING CALIBRATION.
    min_scores: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        values = [getattr(self, name) for name in _CORE_FIELDS] + list(self.min_scores.values())
        for value in values:
            if value is not None and (not math.isfinite(value) or value < 0):
                raise InvalidPolicy("requisitos de qualidade devem ser números finitos >= 0")
        if self.min_face_ratio is not None and self.min_face_ratio > 1:
            raise InvalidPolicy("min_face_ratio deve estar entre 0 e 1")
        if (
            self.min_brightness is not None
            and self.max_brightness is not None
            and self.min_brightness > self.max_brightness
        ):
            raise InvalidPolicy("min_brightness maior que max_brightness")

    def pending(self) -> tuple[str, ...]:
        """Critérios centrais ainda sem valor (PENDING CALIBRATION)."""
        return tuple(name for name in _CORE_FIELDS if getattr(self, name) is None)


class QualityGate:
    def __init__(self, requirements: QualityRequirements) -> None:
        self.requirements = requirements

    def considered_faces(self, detection: FaceDetectionResult) -> tuple[DetectedFace, ...]:
        """Rostos que contam para a regra de quantidade.

        Com `min_face_px` configurado, rostos menores (ex.: pessoas ao fundo) são
        ignorados. Sem ele, todos contam.
        """
        minimum = self.requirements.min_face_px
        if minimum is None:
            return detection.faces
        return tuple(f for f in detection.faces if f.box.min_side >= minimum)

    def face_count_rejection(
        self, detection: FaceDetectionResult
    ) -> tuple[RejectionReason, QualityReport] | None:
        faces = self.considered_faces(detection)
        if len(faces) == 1:
            return None
        if not faces and detection.faces:
            # Há rosto, mas nenhum com o tamanho mínimo.
            report = QualityReport(passed=False, issues=(QualityIssue.FACE_TOO_SMALL.value,))
            return RejectionReason.LOW_QUALITY, report
        if not faces:
            return RejectionReason.NO_FACE, QualityReport(
                passed=False, issues=(QualityIssue.NO_FACE.value,)
            )
        return RejectionReason.MULTIPLE_FACES, QualityReport(
            passed=False, issues=(QualityIssue.MULTIPLE_FACES.value,)
        )

    def evaluate(
        self,
        detection: FaceDetectionResult,
        face: DetectedFace,
        measurements: QualityMeasurements,
    ) -> QualityReport:
        req = self.requirements
        issues: list[str] = []
        min_side = face.box.min_side
        ratio = face.box.area / detection.image_area if detection.image_area else None

        if req.min_face_px is not None and min_side < req.min_face_px:
            issues.append(QualityIssue.FACE_TOO_SMALL.value)
        if req.min_face_ratio is not None:
            if ratio is None:
                raise QualityMeasurementUnavailable("dimensões da imagem ausentes")
            if ratio < req.min_face_ratio:
                issues.append(QualityIssue.FACE_TOO_SMALL_RELATIVE.value)
        if req.min_sharpness is not None:
            sharpness = _required(measurements.sharpness, "sharpness")
            if sharpness < req.min_sharpness:
                issues.append(QualityIssue.BLURRY.value)
        if req.min_brightness is not None or req.max_brightness is not None:
            brightness = _required(measurements.brightness, "brightness")
            if req.min_brightness is not None and brightness < req.min_brightness:
                issues.append(QualityIssue.TOO_DARK.value)
            if req.max_brightness is not None and brightness > req.max_brightness:
                issues.append(QualityIssue.TOO_BRIGHT.value)
        for name, minimum in sorted(req.min_scores.items()):
            value = _required(measurements.scores.get(name), name)
            if value < minimum:
                issues.append(f"{QualityIssue.SCORE_BELOW_MINIMUM.value}:{name}")

        return QualityReport(
            passed=not issues,
            issues=tuple(issues),
            face_min_side_px=round(min_side, 1),
            face_ratio=round(ratio, 4) if ratio is not None else None,
            sharpness=measurements.sharpness,
            brightness=measurements.brightness,
        )


def _required(value: float | None, name: str) -> float:
    if value is None:
        raise QualityMeasurementUnavailable(f"medição '{name}' indisponível")
    return value
