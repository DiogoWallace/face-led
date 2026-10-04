"""Contratos biométricos: uma porta por responsabilidade.

    FaceDetector         encontra rostos (caixa, confiança, 5 landmarks)
    FaceQualityAssessor  MEDE a qualidade de um rosto (quem decide é o QualityGate do domínio)
    FaceEmbedder         gera o embedding/template de um rosto já detectado
    FaceComparator       similaridade entre embeddings do mesmo modelo
                         (quem decide é a FaceMatchPolicy)
    LivenessProvider     prova de vida, independente do reconhecimento facial

Cada porta pode ser trocada isoladamente (ex.: YuNet + SFace + liveness de
fornecedor). Implementações vivem em app/infrastructure/biometric/.
Nenhum threshold de decisão pertence a estas portas.
"""

from dataclasses import dataclass
from typing import Protocol

from app.domain.value_objects import (
    CaptureData,
    DetectedFace,
    FaceDetectionResult,
    FaceEmbedding,
    LivenessEvidence,
    LivenessResult,
    QualityMeasurements,
)


class BiometricProviderError(Exception):
    """Falha de um componente biométrico (indisponível, timeout, entrada inválida)."""


class BiometricProviderNotConfigured(BiometricProviderError):
    """O componente não foi configurado para este ambiente."""


class LivenessNotConfigured(BiometricProviderNotConfigured):
    """Nenhum LivenessProvider real configurado (PENDING DECISION)."""


class FaceDetector(Protocol):
    @property
    def name(self) -> str: ...

    async def detect(self, capture: CaptureData) -> FaceDetectionResult: ...


class FaceQualityAssessor(Protocol):
    @property
    def name(self) -> str: ...

    async def measure(self, capture: CaptureData, face: DetectedFace) -> QualityMeasurements: ...


class FaceEmbedder(Protocol):
    @property
    def name(self) -> str: ...

    async def embed(self, capture: CaptureData, face: DetectedFace) -> FaceEmbedding: ...


class FaceComparator(Protocol):
    @property
    def name(self) -> str: ...

    async def similarity(self, probe: FaceEmbedding, reference: FaceEmbedding) -> float:
        """Maior = mais parecido. Deve falhar se os modelos dos embeddings forem diferentes."""
        ...


class LivenessProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def check(self, evidence: LivenessEvidence) -> LivenessResult: ...


@dataclass(frozen=True, slots=True)
class FaceAnalysisComponents:
    """Agrupa os componentes escolhidos na composição. Não contém lógica."""

    detector: FaceDetector
    quality_assessor: FaceQualityAssessor
    embedder: FaceEmbedder
    comparator: FaceComparator
    liveness: LivenessProvider

    def describe(self) -> dict[str, str]:
        """Nomes dos componentes, para eventos e logs (sem dados biométricos)."""
        return {
            "detector": self.detector.name,
            "quality_assessor": self.quality_assessor.name,
            "embedder": self.embedder.name,
            "comparator": self.comparator.name,
            "liveness": self.liveness.name,
        }
