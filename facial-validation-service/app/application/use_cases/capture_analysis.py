"""Pipeline comum a cadastro e validação, orquestrando portas independentes:

    FaceDetector → QualityGate (quantidade de rostos) → FaceQualityAssessor
    → QualityGate (critérios) → LivenessProvider → FaceEmbedder

Liveness e embedding só rodam para capturas aprovadas no gate. Este módulo não
conhece nenhuma biblioteca ou fornecedor.
"""

from dataclasses import dataclass

from app.application.ports import (
    BiometricProviderError,
    BiometricProviderNotConfigured,
    FaceAnalysisComponents,
    LivenessNotConfigured,
)
from app.domain.services import LivenessRequirement, QualityGate, QualityMeasurementUnavailable
from app.domain.value_objects import (
    CaptureData,
    ErrorReason,
    FaceEmbedding,
    LivenessEvidence,
    LivenessResult,
    LivenessVerdict,
    QualityReport,
    RejectionReason,
)


@dataclass(frozen=True, slots=True)
class CaptureAnalysis:
    embedding: FaceEmbedding | None = None
    liveness: LivenessResult | None = None
    quality: QualityReport | None = None
    rejection: RejectionReason | None = None
    error: ErrorReason | None = None


class FaceAnalysisPipeline:
    def __init__(
        self,
        *,
        components: FaceAnalysisComponents,
        quality_gate: QualityGate,
        liveness_requirement: LivenessRequirement,
    ) -> None:
        self.components = components
        self.quality_gate = quality_gate
        self.liveness_requirement = liveness_requirement

    async def analyze(
        self, capture: CaptureData, liveness_evidence: LivenessEvidence | None = None
    ) -> CaptureAnalysis:
        c = self.components
        try:
            detection = await c.detector.detect(capture)
            count_rejection = self.quality_gate.face_count_rejection(detection)
            if count_rejection is not None:
                reason, report = count_rejection
                return CaptureAnalysis(rejection=reason, quality=report)
            face = self.quality_gate.considered_faces(detection)[0]

            measurements = await c.quality_assessor.measure(capture, face)
            quality = self.quality_gate.evaluate(detection, face, measurements)
            if not quality.passed:
                return CaptureAnalysis(rejection=RejectionReason.LOW_QUALITY, quality=quality)

            liveness = None
            if self.liveness_requirement is LivenessRequirement.REQUIRED:
                liveness = await c.liveness.check(
                    liveness_evidence or LivenessEvidence(capture=capture)
                )
                if liveness.verdict is LivenessVerdict.SPOOF:
                    return CaptureAnalysis(
                        liveness=liveness,
                        quality=quality,
                        rejection=RejectionReason.LIVENESS_FAILED,
                    )
                if liveness.verdict is LivenessVerdict.INCONCLUSIVE:
                    return CaptureAnalysis(
                        liveness=liveness, quality=quality, error=ErrorReason.LIVENESS_INCONCLUSIVE
                    )

            embedding = await c.embedder.embed(capture, face)
            return CaptureAnalysis(embedding=embedding, liveness=liveness, quality=quality)
        except QualityMeasurementUnavailable:
            return CaptureAnalysis(error=ErrorReason.QUALITY_MEASUREMENT_UNAVAILABLE)
        except LivenessNotConfigured:
            return CaptureAnalysis(error=ErrorReason.LIVENESS_NOT_CONFIGURED)
        except BiometricProviderNotConfigured:
            return CaptureAnalysis(error=ErrorReason.PROVIDER_NOT_CONFIGURED)
        except BiometricProviderError:
            return CaptureAnalysis(error=ErrorReason.PROVIDER_FAILURE)
