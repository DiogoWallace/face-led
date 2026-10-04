import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.entities import FaceRegistration, Verification
from app.domain.exceptions import InvalidPolicy, InvalidStateTransition, PolicyModelMismatch
from app.domain.services import FaceMatchPolicy, FaceMatchPolicyRegistry, MatchDecision
from app.domain.value_objects import (
    CaptureData,
    Decision,
    ErrorReason,
    FaceEmbedding,
    ProcessStatus,
    RejectionReason,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_verification(**overrides) -> Verification:
    data = dict(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        subject_id=uuid.uuid4(),
        idempotency_key="key-00000001",
        capture_object_key="k",
        expires_at=NOW + timedelta(minutes=15),
        now=NOW,
    )
    data.update(overrides)
    return Verification.create(**data)


class TestStateTransitions:
    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (ProcessStatus.CREATED, ProcessStatus.PROCESSING),
            (ProcessStatus.CREATED, ProcessStatus.ERROR),
            (ProcessStatus.CREATED, ProcessStatus.EXPIRED),
            (ProcessStatus.PROCESSING, ProcessStatus.APPROVED),
            (ProcessStatus.PROCESSING, ProcessStatus.REJECTED),
            (ProcessStatus.PROCESSING, ProcessStatus.ERROR),
        ],
    )
    def test_allowed(self, current, target):
        v = make_verification()
        v.status = current
        v._transition(target, NOW)
        assert v.status is target

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (ProcessStatus.CREATED, ProcessStatus.APPROVED),
            (ProcessStatus.CREATED, ProcessStatus.REJECTED),
            (ProcessStatus.APPROVED, ProcessStatus.REJECTED),
            (ProcessStatus.REJECTED, ProcessStatus.APPROVED),
            (ProcessStatus.ERROR, ProcessStatus.PROCESSING),
            (ProcessStatus.EXPIRED, ProcessStatus.PROCESSING),
        ],
    )
    def test_forbidden(self, current, target):
        v = make_verification()
        v.status = current
        with pytest.raises(InvalidStateTransition):
            v._transition(target, NOW)

    def test_terminal_states(self):
        assert {s for s in ProcessStatus if s.is_terminal} == {
            ProcessStatus.APPROVED,
            ProcessStatus.REJECTED,
            ProcessStatus.ERROR,
            ProcessStatus.EXPIRED,
        }


class TestRejectedIsNotError:
    def test_reject_sets_rejected_decision(self):
        v = make_verification()
        v.start_processing(NOW)
        v.reject(RejectionReason.LIVENESS_FAILED, NOW)
        assert v.status is ProcessStatus.REJECTED
        assert v.decision is Decision.REJECTED
        assert v.reason == "LIVENESS_FAILED"

    def test_fail_sets_error_decision(self):
        v = make_verification()
        v.start_processing(NOW)
        v.fail(ErrorReason.PROVIDER_FAILURE, NOW)
        assert v.status is ProcessStatus.ERROR
        assert v.decision is Decision.ERROR

    def test_expired_maps_to_error_decision(self):
        v = make_verification()
        v.expire(NOW)
        assert v.decision is Decision.ERROR
        assert v.reason == ErrorReason.EXPIRED

    def test_in_progress_has_no_decision(self):
        v = make_verification()
        assert v.decision is None
        v.start_processing(NOW)
        assert v.decision is None


class TestVerification:
    def test_record_match_approved(self):
        v = make_verification()
        v.start_processing(NOW)
        reg_id = uuid.uuid4()
        v.record_match(
            matched=True, face_registration_id=reg_id, similarity=0.9, policy_version="p1", now=NOW
        )
        assert v.status is ProcessStatus.APPROVED
        assert v.policy_version == "p1"
        assert v.face_registration_id == reg_id

    def test_record_match_rejected_as_mismatch(self):
        v = make_verification()
        v.start_processing(NOW)
        v.record_match(
            matched=False,
            face_registration_id=uuid.uuid4(),
            similarity=0.1,
            policy_version="p1",
            now=NOW,
        )
        assert v.status is ProcessStatus.REJECTED
        assert v.reason == RejectionReason.FACE_MISMATCH

    def test_expiration(self):
        v = make_verification()
        assert not v.is_expired(NOW)
        assert v.is_expired(NOW + timedelta(minutes=15))


class TestFaceRegistration:
    def test_approve_stores_template_and_model(self):
        r = FaceRegistration.create(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            subject_id=uuid.uuid4(),
            capture_object_key="k",
            now=NOW,
        )
        assert not r.is_active_reference
        r.start_processing(NOW)
        r.approve(template=b"cipher", model_name="m", model_version="1", now=NOW)
        assert r.is_active_reference
        assert (r.model_name, r.model_version) == ("m", "1")

    def test_repr_never_contains_template(self):
        r = FaceRegistration.create(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            subject_id=uuid.uuid4(),
            capture_object_key="k",
            now=NOW,
        )
        r.template = b"SECRET-TEMPLATE"
        assert "SECRET-TEMPLATE" not in repr(r)


class TestSensitiveValueObjects:
    def test_capture_repr_hides_content(self):
        assert "SECRET" not in repr(CaptureData(content=b"SECRET", content_type="image/jpeg"))

    def test_embedding_repr_hides_vector(self):
        e = FaceEmbedding(vector=b"SECRET", model_name="m", model_version="1")
        assert "SECRET" not in repr(e)


class TestFaceMatchPolicy:
    policy = FaceMatchPolicy(version="v1", model_name="m", model_version="1", min_similarity=0.7)

    def test_match_at_threshold(self):
        assert self.policy.decide(0.7, model_name="m", model_version="1") is MatchDecision.MATCH

    def test_no_match_below_threshold(self):
        assert (
            self.policy.decide(0.6999, model_name="m", model_version="1") is MatchDecision.NO_MATCH
        )

    def test_rejects_other_model(self):
        with pytest.raises(PolicyModelMismatch):
            self.policy.decide(0.99, model_name="m", model_version="2")

    def test_rejects_nan_similarity(self):
        with pytest.raises(InvalidPolicy):
            self.policy.decide(float("nan"), model_name="m", model_version="1")

    @pytest.mark.parametrize("threshold", [float("nan"), float("inf")])
    def test_invalid_threshold(self, threshold):
        with pytest.raises(InvalidPolicy):
            FaceMatchPolicy(
                version="v", model_name="m", model_version="1", min_similarity=threshold
            )

    def test_registry_versioning(self):
        registry = FaceMatchPolicyRegistry()
        assert registry.active is None
        v2 = FaceMatchPolicy(version="v2", model_name="m", model_version="1", min_similarity=0.8)
        registry.register(self.policy)
        registry.register(v2, activate=True)
        assert registry.active == v2
        assert registry.get("v1") == self.policy

    def test_registry_refuses_redefining_version(self):
        registry = FaceMatchPolicyRegistry()
        registry.register(self.policy)
        with pytest.raises(InvalidPolicy):
            registry.register(
                FaceMatchPolicy(version="v1", model_name="m", model_version="1", min_similarity=0.1)
            )


class TestSupersede:
    def approved(self):
        now = datetime(2026, 1, 1, tzinfo=UTC)
        r = FaceRegistration.create(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            subject_id=uuid.uuid4(),
            capture_object_key="k",
            now=now,
        )
        r.start_processing(now)
        r.approve(template=b"t", model_name="m", model_version="1", now=now)
        return r, now

    def test_supersede_erases_template_and_records_who(self):
        r, now = self.approved()
        by = uuid.uuid4()
        r.supersede(by=by, now=now)
        assert r.template is None and r.superseded_by_id == by and r.superseded_at == now
        assert r.status is ProcessStatus.APPROVED and not r.is_active_reference

    def test_only_active_reference_can_be_superseded(self):
        r, now = self.approved()
        r.supersede(by=uuid.uuid4(), now=now)
        with pytest.raises(InvalidStateTransition):
            r.supersede(by=uuid.uuid4(), now=now)
        pending = FaceRegistration.create(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            subject_id=uuid.uuid4(),
            capture_object_key="k",
            now=now,
        )
        with pytest.raises(InvalidStateTransition):
            pending.supersede(by=uuid.uuid4(), now=now)

    def test_cannot_supersede_itself(self):
        r, now = self.approved()
        with pytest.raises(InvalidStateTransition):
            r.supersede(by=r.id, now=now)
