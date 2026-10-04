"""Composição e guards: calibração, liveness e quality gate por ambiente."""

import pytest

from app.config import Settings
from app.domain.exceptions import InvalidPolicy
from app.domain.services import (
    CalibrationStatus,
    FaceMatchPolicy,
    FaceMatchPolicyRegistry,
    LivenessRequirement,
    MatchPurpose,
)
from app.infrastructure.biometric import (
    build_biometric_setup,
    build_components,
    build_liveness_requirement,
    build_policy_registry,
    build_quality_gate,
)
from app.infrastructure.security.template_cipher import generate_key

QUALITY_ALL = dict(
    quality_min_face_px=1,
    quality_min_face_ratio=0.01,
    quality_min_sharpness=1,
    quality_min_brightness=1,
    quality_max_brightness=250,
)
POLICY = dict(
    face_match_policy_version="p1",
    face_match_min_similarity=0.5,
    biometric_model="m",
    biometric_model_version="1",
)


def settings(**overrides) -> Settings:
    base = dict(
        app_env="test",
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://unused",
        s3_bucket="unused",
        s3_access_key="unused",
        s3_secret_key="unused",
        template_encryption_key=generate_key(),
        # Explícitos: o .env do container pode ativar componentes reais (ex.: OpenCV).
        biometric_detector="none",
        biometric_quality_assessor="none",
        biometric_embedder="none",
        biometric_comparator="none",
        liveness_provider="none",
    )
    base.update(overrides)
    return Settings(**base)


class TestComponents:
    def test_defaults_are_unconfigured_and_independent(self):
        described = build_components(settings()).describe()
        assert described == {
            "detector": "none",
            "quality_assessor": "none",
            "embedder": "none",
            "comparator": "none",
            "liveness": "none",
        }

    @pytest.mark.parametrize(
        "variable",
        [
            "biometric_detector",
            "biometric_quality_assessor",
            "biometric_embedder",
            "biometric_comparator",
            "liveness_provider",
        ],
    )
    def test_unknown_component(self, variable):
        with pytest.raises(ValueError):
            build_components(settings(**{variable: "magic"}))


class TestPolicyCalibrationGuard:
    def test_no_policy_when_not_configured(self):
        assert build_policy_registry(settings()).active is None

    def test_uncalibrated_policy_allowed_in_test(self):
        policy = build_policy_registry(settings(**POLICY)).active
        assert policy.calibration_status is CalibrationStatus.PENDING_CALIBRATION
        assert policy.purpose is MatchPurpose.SELFIE_VS_REFERENCE

    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_uncalibrated_policy_refused_outside_local_test(self, env):
        with pytest.raises(InvalidPolicy):
            build_policy_registry(settings(app_env=env, **POLICY))

    def test_calibrated_policy_accepted_in_production(self):
        policy = build_policy_registry(
            settings(
                app_env="production",
                face_match_calibration_status="CALIBRATED",
                face_match_target_far=0.001,
                face_match_calibration_reference="calibracao-2026-xx",
                **POLICY,
            )
        ).active
        assert policy.is_calibrated

    def test_calibrated_requires_target_far_and_reference(self):
        with pytest.raises(InvalidPolicy):
            FaceMatchPolicy(
                version="v",
                model_name="m",
                model_version="1",
                min_similarity=0.5,
                calibration_status=CalibrationStatus.CALIBRATED,
            )

    def test_registry_keeps_one_active_policy_per_purpose(self):
        registry = FaceMatchPolicyRegistry()
        selfie = FaceMatchPolicy(version="s", model_name="m", model_version="1", min_similarity=0.5)
        doc = FaceMatchPolicy(
            version="d",
            model_name="m",
            model_version="1",
            min_similarity=0.4,
            purpose=MatchPurpose.DOCUMENT_VS_SELFIE,
        )
        registry.register(selfie, activate=True)
        registry.register(doc, activate=True)
        assert registry.active_for(MatchPurpose.SELFIE_VS_REFERENCE) == selfie
        assert registry.active_for(MatchPurpose.DOCUMENT_VS_SELFIE) == doc


class TestLivenessRequirementGuard:
    def test_default_is_required(self):
        assert build_liveness_requirement(settings()) is LivenessRequirement.REQUIRED

    def test_evaluation_mode_allowed_in_local(self):
        requirement = build_liveness_requirement(
            settings(app_env="local", liveness_requirement="DISABLED_FOR_EVALUATION")
        )
        assert requirement is LivenessRequirement.DISABLED_FOR_EVALUATION

    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_evaluation_mode_refused_elsewhere(self, env):
        with pytest.raises(InvalidPolicy):
            build_liveness_requirement(
                settings(app_env=env, liveness_requirement="DISABLED_FOR_EVALUATION", **QUALITY_ALL)
            )

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            build_liveness_requirement(settings(liveness_requirement="MAYBE"))


class TestQualityGateGuard:
    def test_pending_criteria_allowed_in_test(self):
        assert build_quality_gate(settings()).requirements.pending()

    def test_pending_criteria_refused_in_production(self):
        with pytest.raises(InvalidPolicy):
            build_quality_gate(settings(app_env="production", quality_min_face_px=40))

    def test_fully_configured_accepted_in_production(self):
        gate = build_quality_gate(settings(app_env="production", **QUALITY_ALL))
        assert gate.requirements.pending() == ()


def test_full_setup_in_test_env():
    setup = build_biometric_setup(settings(**POLICY))
    assert setup.pipeline.liveness_requirement is LivenessRequirement.REQUIRED
    assert setup.policies.active is not None
