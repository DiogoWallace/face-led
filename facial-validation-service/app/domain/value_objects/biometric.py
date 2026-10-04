"""Value objects biométricos independentes de biblioteca ou fornecedor.

- Detecção, qualidade, embedding, comparação e liveness são responsabilidades
  separadas (ver docs/decisions/ADR-006-biometric-components.md).
- O embedding/template é tratado como bytes opacos: o formato depende do modelo,
  identificado por `model_name` e `model_version`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class CaptureData:
    content: bytes = field(repr=False)
    content_type: str

    def __repr__(self) -> str:  # nunca expõe o conteúdo em logs/tracebacks
        return f"CaptureData(content_type={self.content_type!r}, size={len(self.content)})"


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class BoundingBox:
    x: float
    y: float
    width: float
    height: float

    @property
    def min_side(self) -> float:
        return min(self.width, self.height)

    @property
    def area(self) -> float:
        return max(self.width, 0.0) * max(self.height, 0.0)


@dataclass(frozen=True, slots=True)
class FaceLandmarks:
    """5 pontos genéricos, na perspectiva da pessoa fotografada."""

    right_eye: Point
    left_eye: Point
    nose_tip: Point
    mouth_right: Point
    mouth_left: Point


@dataclass(frozen=True, slots=True)
class DetectedFace:
    box: BoundingBox
    confidence: float | None = None
    landmarks: FaceLandmarks | None = None


@dataclass(frozen=True, slots=True)
class FaceDetectionResult:
    faces: tuple[DetectedFace, ...]
    image_width: int
    image_height: int

    @property
    def face_count(self) -> int:
        return len(self.faces)

    @property
    def image_area(self) -> int:
        return max(self.image_width, 0) * max(self.image_height, 0)


class QualityIssue(StrEnum):
    NO_FACE = "NO_FACE"
    MULTIPLE_FACES = "MULTIPLE_FACES"
    FACE_TOO_SMALL = "FACE_TOO_SMALL"
    FACE_TOO_SMALL_RELATIVE = "FACE_TOO_SMALL_RELATIVE"
    BLURRY = "BLURRY"
    TOO_DARK = "TOO_DARK"
    TOO_BRIGHT = "TOO_BRIGHT"
    SCORE_BELOW_MINIMUM = "SCORE_BELOW_MINIMUM"


@dataclass(frozen=True, slots=True)
class QualityMeasurements:
    """Medições produzidas pelo FaceQualityAssessor. Nada aqui decide aprovação.

    `scores` acomoda medidores futuros (ex.: "fiqa", "occlusion", "pose") sem
    alterar o contrato; os nomes são definidos por configuração.
    """

    sharpness: float | None = None
    brightness: float | None = None
    scores: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class QualityReport:
    passed: bool
    issues: tuple[str, ...] = ()
    face_min_side_px: float | None = None
    face_ratio: float | None = None
    sharpness: float | None = None
    brightness: float | None = None

    def as_audit_data(self) -> dict[str, object]:
        """Somente códigos e medidas agregadas: seguro para eventos e logs."""
        return {
            "passed": self.passed,
            "issues": list(self.issues),
            "face_min_side_px": self.face_min_side_px,
            "face_ratio": self.face_ratio,
            "sharpness": self.sharpness,
            "brightness": self.brightness,
        }


class LivenessVerdict(StrEnum):
    LIVE = "LIVE"
    SPOOF = "SPOOF"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class LivenessEvidence:
    """O que o LivenessProvider analisa.

    Hoje: a própria captura. Futuramente: vídeo/quadros ou a referência de uma
    sessão de fornecedor (AWS/Azure) ou de um desafio ativo. PENDING DECISION.
    """

    capture: CaptureData | None = None
    session_reference: str | None = None
    # Liveness ativo (ADR-010): quadros capturados durante o desafio e os passos sorteados.
    frames: tuple[CaptureData, ...] = ()
    challenge: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.capture is None and not self.session_reference:
            raise ValueError("LivenessEvidence exige captura ou referência de sessão")


@dataclass(frozen=True, slots=True)
class LivenessResult:
    verdict: LivenessVerdict
    score: float | None = None
    # Código curto do porquê (ex.: STEP_2_TURN_LEFT_NOT_DONE). Auditoria e calibração;
    # não é exposto na API (ADR-007).
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class FaceEmbedding:
    vector: bytes = field(repr=False)
    model_name: str
    model_version: str

    def __repr__(self) -> str:
        return (
            f"FaceEmbedding(model_name={self.model_name!r}, "
            f"model_version={self.model_version!r}, size={len(self.vector)})"
        )

    def same_model_as(self, other: "FaceEmbedding") -> bool:
        return (self.model_name, self.model_version) == (other.model_name, other.model_version)
