"""Composição dos componentes biométricos a partir da configuração.

Único ponto que conhece as implementações concretas. Cada componente é
escolhido de forma independente; novos adapters (AWS, Azure, liveness ativo)
são registrados nos catálogos abaixo, como os OpenCV (`opencv-yunet`,
`opencv`, `opencv-sface`).

Montar os componentes não carrega modelo nenhum: isso acontece em
`BiometricSetup.start()`, que só o worker chama. A API monta o mesmo setup e
nunca carrega.

Regras de segurança aplicadas na inicialização, fora de APP_ENV=local|test:
- FaceMatchPolicy PENDING_CALIBRATION não pode ser ativada;
- LIVENESS_REQUIREMENT=DISABLED_FOR_EVALUATION é recusado;
- todos os critérios centrais do QualityGate precisam estar configurados.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.application.ports import (
    FaceAnalysisComponents,
    FaceComparator,
    FaceDetector,
    FaceEmbedder,
    FaceQualityAssessor,
    LivenessProvider,
)
from app.application.use_cases.capture_analysis import FaceAnalysisPipeline
from app.config import Settings
from app.domain.exceptions import InvalidPolicy
from app.domain.services import (
    ActiveLivenessParameters,
    CalibrationStatus,
    FaceMatchPolicy,
    FaceMatchPolicyRegistry,
    LivenessRequirement,
    MatchPurpose,
    QualityGate,
    QualityRequirements,
    ensure_liveness_requirement_allowed,
)
from app.infrastructure.biometric.liveness import ActiveChallengeLivenessProvider
from app.infrastructure.biometric.opencv import (
    OpenCVQualityAssessor,
    OpenCVRuntime,
    SFaceCosineComparator,
    SFaceEmbedder,
    YuNetFaceDetector,
)
from app.infrastructure.biometric.unconfigured import (
    UnconfiguredFaceComparator,
    UnconfiguredFaceDetector,
    UnconfiguredFaceEmbedder,
    UnconfiguredLivenessProvider,
    UnconfiguredQualityAssessor,
)

EVALUATION_ENVIRONMENTS = ("local", "test")


@dataclass
class ComponentContext:
    """Recursos compartilhados entre componentes do mesmo motor.

    Detector e embedder OpenCV usam o MESMO runtime (um executor, e em cada
    thread uma instância de cada modelo requerido). Criar o runtime não carrega
    nada: os modelos só são lidos em `BiometricSetup.start()`, chamado pelo worker.
    """

    settings: Settings
    _opencv: OpenCVRuntime | None = field(default=None, init=False)

    def opencv(self) -> OpenCVRuntime:
        if self._opencv is None:
            s = self.settings
            self._opencv = OpenCVRuntime(
                models_dir=Path(s.biometric_models_dir),
                inference_threads=s.biometric_inference_threads,
                opencv_threads=s.biometric_opencv_threads,
                detector_score_threshold=s.biometric_detector_score_threshold,
            )
        return self._opencv

    def runtimes(self) -> tuple[OpenCVRuntime, ...]:
        return (self._opencv,) if self._opencv is not None else ()


DETECTORS: dict[str, Callable[[ComponentContext], FaceDetector]] = {
    "none": lambda _: UnconfiguredFaceDetector(),
    "opencv-yunet": lambda ctx: YuNetFaceDetector(ctx.opencv()),
}
QUALITY_ASSESSORS: dict[str, Callable[[ComponentContext], FaceQualityAssessor]] = {
    "none": lambda _: UnconfiguredQualityAssessor(),
    "opencv": lambda ctx: OpenCVQualityAssessor(ctx.opencv()),
}
EMBEDDERS: dict[str, Callable[[ComponentContext], FaceEmbedder]] = {
    "none": lambda _: UnconfiguredFaceEmbedder(),
    "opencv-sface": lambda ctx: SFaceEmbedder(ctx.opencv()),
}
COMPARATORS: dict[str, Callable[[ComponentContext], FaceComparator]] = {
    "none": lambda _: UnconfiguredFaceComparator(),
    "opencv-sface": lambda _: SFaceCosineComparator(),
}
LIVENESS_PROVIDERS: dict[str, Callable[[ComponentContext], LivenessProvider]] = {
    "none": lambda _: UnconfiguredLivenessProvider(),
    "active": lambda ctx: ActiveChallengeLivenessProvider(
        ctx.opencv(),
        active_liveness_parameters(ctx.settings),
        ctx.settings.liveness_same_person_min_similarity,
    ),
}


def active_liveness_parameters(s: Settings) -> ActiveLivenessParameters:
    return ActiveLivenessParameters(
        min_frames=s.liveness_min_frames,
        min_tracked_ratio=s.liveness_min_tracked_ratio,
        frontal_max_yaw=s.liveness_frontal_max_yaw,
        turn_min_yaw=s.liveness_turn_min_yaw,
        turn_min_eye_ratio=s.liveness_turn_min_eye_ratio,
        closer_min_scale=s.liveness_closer_min_scale,
        max_yaw_jump=s.liveness_max_yaw_jump,
        max_scale_jump=s.liveness_max_scale_jump,
    )


def ensure_active_liveness_allowed(settings: Settings) -> None:
    """Guard (ADR-010): sem APCER/BPCER medidos, o liveness ativo só roda em local/test."""
    if settings.liveness_provider != "active" or is_evaluation_environment(settings):
        return
    if settings.liveness_calibration_status != "CALIBRATED":
        raise InvalidPolicy(
            "LIVENESS_PROVIDER=active com LIVENESS_CALIBRATION_STATUS PENDING fora de local/test"
        )
    if settings.liveness_same_person_min_similarity is None:
        raise InvalidPolicy("LIVENESS_SAME_PERSON_MIN_SIMILARITY obrigatório fora de local/test")


def _pick[T](
    catalog: dict[str, Callable[[ComponentContext], T]],
    key: str,
    variable: str,
    ctx: ComponentContext,
) -> T:
    try:
        factory = catalog[key]
    except KeyError:
        raise ValueError(f"{variable} desconhecido: {key!r}") from None
    return factory(ctx)


def is_evaluation_environment(settings: Settings) -> bool:
    return settings.app_env in EVALUATION_ENVIRONMENTS


def build_components(
    settings: Settings, ctx: ComponentContext | None = None
) -> FaceAnalysisComponents:
    ctx = ctx or ComponentContext(settings)
    return FaceAnalysisComponents(
        detector=_pick(DETECTORS, settings.biometric_detector, "BIOMETRIC_DETECTOR", ctx),
        quality_assessor=_pick(
            QUALITY_ASSESSORS,
            settings.biometric_quality_assessor,
            "BIOMETRIC_QUALITY_ASSESSOR",
            ctx,
        ),
        embedder=_pick(EMBEDDERS, settings.biometric_embedder, "BIOMETRIC_EMBEDDER", ctx),
        comparator=_pick(COMPARATORS, settings.biometric_comparator, "BIOMETRIC_COMPARATOR", ctx),
        liveness=_pick(LIVENESS_PROVIDERS, settings.liveness_provider, "LIVENESS_PROVIDER", ctx),
    )


def build_quality_gate(settings: Settings) -> QualityGate:
    requirements = QualityRequirements(
        min_face_px=settings.quality_min_face_px,
        min_face_ratio=settings.quality_min_face_ratio,
        min_sharpness=settings.quality_min_sharpness,
        min_brightness=settings.quality_min_brightness,
        max_brightness=settings.quality_max_brightness,
    )
    if requirements.pending() and not is_evaluation_environment(settings):
        raise InvalidPolicy(
            "QualityGate com critérios PENDING CALIBRATION fora de local/test: "
            + ", ".join(requirements.pending())
        )
    return QualityGate(requirements)


def build_liveness_requirement(settings: Settings) -> LivenessRequirement:
    try:
        requirement = LivenessRequirement(settings.liveness_requirement.upper())
    except ValueError:
        raise ValueError(
            f"LIVENESS_REQUIREMENT inválido: {settings.liveness_requirement!r}"
        ) from None
    ensure_liveness_requirement_allowed(
        requirement, evaluation_environment=is_evaluation_environment(settings)
    )
    ensure_active_liveness_allowed(settings)
    return requirement


def build_policy_registry(settings: Settings) -> FaceMatchPolicyRegistry:
    registry = FaceMatchPolicyRegistry(allow_uncalibrated=is_evaluation_environment(settings))
    required = (
        settings.face_match_policy_version,
        settings.face_match_min_similarity,
        settings.biometric_model,
        settings.biometric_model_version,
    )
    if all(value is not None and value != "" for value in required):
        registry.register(
            FaceMatchPolicy(
                version=settings.face_match_policy_version,  # type: ignore[arg-type]
                model_name=settings.biometric_model,  # type: ignore[arg-type]
                model_version=settings.biometric_model_version,  # type: ignore[arg-type]
                min_similarity=settings.face_match_min_similarity,  # type: ignore[arg-type]
                calibration_status=CalibrationStatus(
                    settings.face_match_calibration_status.upper()
                ),
                target_far=settings.face_match_target_far,
                purpose=MatchPurpose.SELFIE_VS_REFERENCE,
                calibration_reference=settings.face_match_calibration_reference,
            ),
            activate=True,
        )
    return registry


@dataclass(frozen=True, slots=True)
class BiometricSetup:
    pipeline: FaceAnalysisPipeline
    policies: FaceMatchPolicyRegistry
    runtimes: tuple[OpenCVRuntime, ...] = ()

    @property
    def loaded_models(self) -> tuple[str, ...]:
        return tuple(name for r in self.runtimes for name in r.required_models)

    async def start(self) -> None:
        """Carrega os modelos (só o worker chama). Falha impede o processo de subir."""
        for runtime in self.runtimes:
            await runtime.start()

    async def close(self) -> None:
        for runtime in self.runtimes:
            await runtime.close()


def build_biometric_setup(settings: Settings) -> BiometricSetup:
    ctx = ComponentContext(settings)
    return BiometricSetup(
        pipeline=FaceAnalysisPipeline(
            components=build_components(settings, ctx),
            quality_gate=build_quality_gate(settings),
            liveness_requirement=build_liveness_requirement(settings),
        ),
        policies=build_policy_registry(settings),
        runtimes=ctx.runtimes(),
    )
