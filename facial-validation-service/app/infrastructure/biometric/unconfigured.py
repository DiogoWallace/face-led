"""Implementações "não configurado" de cada porta biométrica.

Usadas enquanto um componente real não é escolhido. Todo processamento que
precisar delas termina em ERROR (PROVIDER_NOT_CONFIGURED ou
LIVENESS_NOT_CONFIGURED): o serviço nunca aprova ou rejeita alguém sem um
componente real avaliado.
"""

from app.application.ports import BiometricProviderNotConfigured, LivenessNotConfigured
from app.domain.value_objects import (
    CaptureData,
    DetectedFace,
    FaceDetectionResult,
    FaceEmbedding,
    LivenessEvidence,
    LivenessResult,
    QualityMeasurements,
)

NONE = "none"


class UnconfiguredFaceDetector:
    name = NONE

    async def detect(self, capture: CaptureData) -> FaceDetectionResult:
        raise BiometricProviderNotConfigured("BIOMETRIC_DETECTOR não configurado")


class UnconfiguredQualityAssessor:
    name = NONE

    async def measure(self, capture: CaptureData, face: DetectedFace) -> QualityMeasurements:
        raise BiometricProviderNotConfigured("BIOMETRIC_QUALITY_ASSESSOR não configurado")


class UnconfiguredFaceEmbedder:
    name = NONE

    async def embed(self, capture: CaptureData, face: DetectedFace) -> FaceEmbedding:
        raise BiometricProviderNotConfigured("BIOMETRIC_EMBEDDER não configurado")


class UnconfiguredFaceComparator:
    name = NONE

    async def similarity(self, probe: FaceEmbedding, reference: FaceEmbedding) -> float:
        raise BiometricProviderNotConfigured("BIOMETRIC_COMPARATOR não configurado")


class UnconfiguredLivenessProvider:
    name = NONE

    async def check(self, evidence: LivenessEvidence) -> LivenessResult:
        raise LivenessNotConfigured("LIVENESS_PROVIDER não configurado (PENDING DECISION)")
