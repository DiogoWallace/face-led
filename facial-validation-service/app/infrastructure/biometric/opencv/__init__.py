"""Adapters OpenCV: YuNet (detecção), qualidade, SFace (embedding e comparação).

ADR-003 e ADR-006.
"""

from app.infrastructure.biometric.opencv.models import (
    ALL_MODELS,
    SFACE,
    YUNET,
    ModelFile,
    ModelIntegrityError,
    verify_model,
)
from app.infrastructure.biometric.opencv.quality import OpenCVQualityAssessor
from app.infrastructure.biometric.opencv.runtime import OpenCVRuntime
from app.infrastructure.biometric.opencv.sface import SFaceCosineComparator, SFaceEmbedder
from app.infrastructure.biometric.opencv.yunet_detector import YuNetFaceDetector

__all__ = [
    "ALL_MODELS",
    "SFACE",
    "YUNET",
    "ModelFile",
    "ModelIntegrityError",
    "OpenCVQualityAssessor",
    "OpenCVRuntime",
    "SFaceCosineComparator",
    "SFaceEmbedder",
    "YuNetFaceDetector",
    "verify_model",
]
