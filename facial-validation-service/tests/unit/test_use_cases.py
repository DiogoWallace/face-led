"""Orquestração dos casos de uso com dublês. Não valida reconhecimento facial."""

import uuid
from dataclasses import replace

import pytest

from app.application.dto import CreateVerificationCommand, RegisterFaceCommand
from app.application.ports import BiometricProviderError, BiometricProviderNotConfigured
from app.application.use_cases.capture_analysis import FaceAnalysisPipeline
from app.domain.entities import FaceRegistration
from app.domain.exceptions import (
    FaceRegistrationNotFound,
    IdempotencyConflict,
    InvalidCapture,
    InvalidStateTransition,
    LivenessEvidenceRequired,
    LivenessSessionInvalid,
    LivenessSessionNotFound,
    SubjectAlreadyEnrolled,
    SubjectNotEnrolled,
    SubjectNotFound,
    VerificationNotFound,
)
from app.domain.services import (
    FaceMatchPolicyRegistry,
    LivenessRequirement,
    QualityGate,
    QualityRequirements,
)
from app.domain.value_objects import (
    CaptureData,
    CaptureRetention,
    Decision,
    LivenessVerdict,
    ProcessStatus,
)
from tests.fakes import JPEG, PNG


def capture(content: bytes = JPEG, content_type: str = "image/jpeg") -> CaptureData:
    return CaptureData(content=content, content_type=content_type)


async def enroll(world, external_id: str = "user-1"):
    accepted = await world.register_face().execute(
        RegisterFaceCommand(
            tenant_id=world.tenant.id, external_subject_id=external_id, capture=capture()
        )
    )
    status = await world.process_registration().execute(accepted.registration_id)
    return accepted, status


async def verify(world, external_id: str = "user-1", key: str = "idem-key-0001"):
    return await world.create_verification().execute(
        CreateVerificationCommand(
            tenant_id=world.tenant.id,
            external_subject_id=external_id,
            idempotency_key=key,
            capture=capture(PNG, "image/png"),
        )
    )


class TestRegisterFace:
    async def test_creates_subject_registration_and_enqueues(self, world):
        accepted = await world.register_face().execute(
            RegisterFaceCommand(
                tenant_id=world.tenant.id, external_subject_id="user-1", capture=capture()
            )
        )
        assert accepted.status is ProcessStatus.CREATED
        assert world.queue.registrations == [accepted.registration_id]
        assert len(world.store.subjects) == 1
        key = world.store.registrations[accepted.registration_id].capture_object_key
        assert key in world.storage.objects
        assert "user-1" not in key  # external_id não vai para a chave do storage

    @pytest.mark.parametrize(
        ("content", "content_type"),
        [
            (JPEG, "image/gif"),
            (b"", "image/jpeg"),
            (PNG, "image/jpeg"),  # assinatura não confere
            (JPEG + b"\x00" * 2048, "image/jpeg"),  # acima do limite
        ],
    )
    async def test_invalid_capture(self, world, content, content_type):
        with pytest.raises(InvalidCapture):
            await world.register_face().execute(
                RegisterFaceCommand(
                    tenant_id=world.tenant.id,
                    external_subject_id="user-1",
                    capture=capture(content, content_type),
                )
            )
        assert world.queue.registrations == []

    async def test_refuses_second_active_registration(self, world):
        await enroll(world)
        with pytest.raises(SubjectAlreadyEnrolled):
            await enroll(world)


class TestProcessFaceRegistration:
    async def test_full_pipeline_approves_and_encrypts_template(self, world):
        accepted, status = await enroll(world)
        assert status is ProcessStatus.APPROVED
        registration = world.store.registrations[accepted.registration_id]
        assert registration.template is not None
        assert b"scripted-embedding" not in registration.template
        assert (registration.model_name, registration.model_version) == ("scripted-model", "1")
        assert world.script.calls == [
            "detect_face",
            "assess_quality",
            "check_liveness",
            "generate_embedding",
        ]
        assert len(world.store.liveness) == 1

    @pytest.mark.parametrize(
        ("setup", "reason"),
        [
            ({"face_count": 0}, "NO_FACE"),
            ({"face_count": 2}, "MULTIPLE_FACES"),
            ({"quality_ok": False}, "LOW_QUALITY"),
            ({"liveness": LivenessVerdict.SPOOF}, "LIVENESS_FAILED"),
        ],
    )
    async def test_rejections(self, world, setup, reason):
        for attr, value in setup.items():
            setattr(world.script, attr, value)
        accepted, status = await enroll(world)
        assert status is ProcessStatus.REJECTED
        assert world.store.registrations[accepted.registration_id].reason == reason
        assert world.store.registrations[accepted.registration_id].template is None

    async def test_inconclusive_liveness_is_error(self, world):
        world.script.liveness = LivenessVerdict.INCONCLUSIVE
        accepted, status = await enroll(world)
        assert status is ProcessStatus.ERROR
        assert world.store.registrations[accepted.registration_id].reason == "LIVENESS_INCONCLUSIVE"

    @pytest.mark.parametrize(
        ("exception", "reason"),
        [
            (BiometricProviderNotConfigured, "PROVIDER_NOT_CONFIGURED"),
            (BiometricProviderError, "PROVIDER_FAILURE"),
            (RuntimeError, "INTERNAL_ERROR"),
        ],
    )
    async def test_provider_failures_are_errors(self, world, exception, reason):
        world.script.raise_on = "detect_face"
        world.script.exception = exception
        accepted, status = await enroll(world)
        assert status is ProcessStatus.ERROR
        assert world.store.registrations[accepted.registration_id].reason == reason

    async def test_storage_unavailable_is_error(self, world):
        accepted = await world.register_face().execute(
            RegisterFaceCommand(
                tenant_id=world.tenant.id, external_subject_id="user-1", capture=capture()
            )
        )
        world.storage.fail = True
        status = await world.process_registration().execute(accepted.registration_id)
        assert status is ProcessStatus.ERROR
        assert world.store.registrations[accepted.registration_id].reason == "CAPTURE_UNAVAILABLE"

    async def test_reprocessing_is_idempotent(self, world):
        accepted, _ = await enroll(world)
        calls = len(world.script.calls)
        status = await world.process_registration().execute(accepted.registration_id)
        assert status is ProcessStatus.APPROVED
        assert len(world.script.calls) == calls


class TestCreateVerification:
    async def test_unknown_subject(self, world):
        with pytest.raises(SubjectNotFound):
            await verify(world, external_id="ghost")

    async def test_subject_without_active_registration(self, world):
        world.script.face_count = 0
        await enroll(world)
        with pytest.raises(SubjectNotEnrolled):
            await verify(world)

    async def test_idempotent_replay(self, world):
        await enroll(world)
        first = await verify(world)
        second = await verify(world)
        assert second.verification_id == first.verification_id
        assert second.replayed is True
        assert world.queue.verifications == [first.verification_id]

    async def test_same_key_other_subject_conflicts(self, world):
        await enroll(world, "user-1")
        await enroll(world, "user-2")
        await verify(world, "user-1")
        with pytest.raises(IdempotencyConflict):
            await verify(world, "user-2")

    async def test_tenant_isolation_on_read(self, world):
        await enroll(world)
        view = await verify(world)
        with pytest.raises(VerificationNotFound):
            await world.get_verification().execute(uuid.uuid4(), view.verification_id)


class TestProcessVerification:
    async def test_same_person_approved(self, world):
        await enroll(world)
        view = await verify(world)
        world.script.similarity = 0.95
        status = await world.process_verification().execute(view.verification_id)
        assert status is ProcessStatus.APPROVED
        result = await world.get_verification().execute(world.tenant.id, view.verification_id)
        assert result.decision is Decision.APPROVED
        assert result.policy_version == "test-v1"

    async def test_different_person_rejected(self, world):
        await enroll(world)
        view = await verify(world)
        world.script.similarity = 0.2
        status = await world.process_verification().execute(view.verification_id)
        assert status is ProcessStatus.REJECTED
        stored = world.store.verifications[view.verification_id]
        assert stored.reason == "FACE_MISMATCH"

    async def test_liveness_spoof_rejected_before_compare(self, world):
        await enroll(world)
        view = await verify(world)
        world.script.liveness = LivenessVerdict.SPOOF
        world.script.calls.clear()
        status = await world.process_verification().execute(view.verification_id)
        assert status is ProcessStatus.REJECTED
        assert "compare" not in world.script.calls

    async def test_without_policy_is_error(self, world):
        await enroll(world)
        view = await verify(world)
        world.policies = FaceMatchPolicyRegistry()
        status = await world.process_verification().execute(view.verification_id)
        assert status is ProcessStatus.ERROR
        assert world.store.verifications[view.verification_id].reason == "POLICY_NOT_CONFIGURED"

    async def test_model_mismatch_is_error(self, world):
        await enroll(world)
        view = await verify(world)
        world.script.model_version = "2"
        status = await world.process_verification().execute(view.verification_id)
        assert status is ProcessStatus.ERROR
        assert world.store.verifications[view.verification_id].reason == "MODEL_MISMATCH"

    async def test_compare_failure_is_error(self, world):
        await enroll(world)
        view = await verify(world)
        world.script.raise_on = "compare"
        world.script.exception = BiometricProviderError
        status = await world.process_verification().execute(view.verification_id)
        assert status is ProcessStatus.ERROR
        assert world.store.verifications[view.verification_id].reason == "PROVIDER_FAILURE"

    async def test_expired_before_processing(self, world):
        await enroll(world)
        view = await verify(world)
        world.clock.advance(minutes=16)
        status = await world.process_verification().execute(view.verification_id)
        assert status is ProcessStatus.EXPIRED
        assert world.store.verifications[view.verification_id].decision is Decision.ERROR

    async def test_events_are_recorded_without_biometric_data(self, world):
        await enroll(world)
        view = await verify(world)
        await world.process_verification().execute(view.verification_id)
        types = [e.event_type for e in world.store.events]
        assert "VERIFICATION_CREATED" in types and "VERIFICATION_APPROVED" in types
        for event in world.store.events:
            assert not ({"embedding", "template", "image", "similarity"} & set(event.data))


class TestPipelineComposition:
    async def test_liveness_not_configured_is_error(self, world):
        from app.infrastructure.biometric.unconfigured import UnconfiguredLivenessProvider

        components = world.script.components()
        world.pipeline = lambda: FaceAnalysisPipeline(
            components=replace(components, liveness=UnconfiguredLivenessProvider()),
            quality_gate=world.quality_gate,
            liveness_requirement=LivenessRequirement.REQUIRED,
        )
        accepted, status = await enroll(world)
        assert status is ProcessStatus.ERROR
        assert (
            world.store.registrations[accepted.registration_id].reason == "LIVENESS_NOT_CONFIGURED"
        )
        assert "generate_embedding" not in world.script.calls

    async def test_evaluation_mode_skips_liveness(self, world):
        world.liveness_requirement = LivenessRequirement.DISABLED_FOR_EVALUATION
        _, status = await enroll(world)
        assert status is ProcessStatus.APPROVED
        assert "check_liveness" not in world.script.calls
        assert world.store.liveness == []

    async def test_background_face_below_min_size_is_ignored(self, world):
        world.script.background_faces = 2
        _, status = await enroll(world)  # min_face_px=40 no World
        assert status is ProcessStatus.APPROVED

    async def test_background_faces_count_without_min_size(self, world):
        world.quality_gate = QualityGate(QualityRequirements(min_sharpness=10))
        world.script.background_faces = 1
        accepted, status = await enroll(world)
        assert status is ProcessStatus.REJECTED
        assert world.store.registrations[accepted.registration_id].reason == "MULTIPLE_FACES"

    async def test_quality_rejection_is_audited_with_issue_codes(self, world):
        world.script.quality_ok = False
        accepted, status = await enroll(world)
        assert status is ProcessStatus.REJECTED
        event = next(e for e in world.store.events if e.event_type == "FACE_REGISTRATION_REJECTED")
        assert event.data["quality"]["issues"] == ["BLURRY"]
        assert event.data["components"]["detector"] == "scripted-detector"
        assert "check_liveness" not in world.script.calls

    async def test_missing_measurement_is_error_not_approval(self, world):
        world.quality_gate = QualityGate(QualityRequirements(min_scores={"fiqa": 0.5}))
        accepted, status = await enroll(world)
        assert status is ProcessStatus.ERROR
        reason = world.store.registrations[accepted.registration_id].reason
        assert reason == "QUALITY_MEASUREMENT_UNAVAILABLE"


class TestCaptureRetention:
    """CAPTURE_RETENTION: PENDING LEGAL/BUSINESS DECISION; KEEP é o padrão original."""

    async def test_keep_leaves_capture_in_storage(self, world):
        accepted, status = await enroll(world)
        assert status is ProcessStatus.APPROVED
        registration = world.store.registrations[accepted.registration_id]
        assert registration.capture_object_key in world.storage.objects
        assert not [e for e in world.store.events if e.event_type == "CAPTURE_DELETED"]

    @pytest.mark.parametrize("quality_ok", [True, False])
    async def test_delete_after_registration_keeps_template(self, world, quality_ok):
        world.capture_retention = CaptureRetention.DELETE_AFTER_PROCESSING
        world.script.quality_ok = quality_ok
        accepted, status = await enroll(world)
        registration = world.store.registrations[accepted.registration_id]

        assert status is (ProcessStatus.APPROVED if quality_ok else ProcessStatus.REJECTED)
        assert registration.capture_object_key is None
        assert world.storage.objects == {}
        assert (registration.template is not None) is quality_ok
        deleted = [e for e in world.store.events if e.event_type == "CAPTURE_DELETED"]
        assert [e.aggregate_id for e in deleted] == [registration.id]
        assert deleted[0].data == {"retention": "DELETE_AFTER_PROCESSING", "liveness_frames": 0}

    async def test_delete_after_verification_and_after_expiration(self, world):
        await enroll(world)
        world.capture_retention = CaptureRetention.DELETE_AFTER_PROCESSING
        done = await verify(world, key="idem-key-0001")
        expired = await verify(world, key="idem-key-0002")
        assert await world.process_verification().execute(done.verification_id) is (
            ProcessStatus.APPROVED
        )
        world.clock.advance(hours=1)
        assert await world.process_verification().execute(expired.verification_id) is (
            ProcessStatus.EXPIRED
        )
        for verification_id in (done.verification_id, expired.verification_id):
            assert world.store.verifications[verification_id].capture_object_key is None
        # Sobra só a captura do cadastro, que foi feito com KEEP.
        assert len(world.storage.objects) == 1

    async def test_storage_failure_keeps_key_and_result(self, world):
        world.capture_retention = CaptureRetention.DELETE_AFTER_PROCESSING
        world.storage.fail_delete = True
        accepted, status = await enroll(world)
        registration = world.store.registrations[accepted.registration_id]

        assert status is ProcessStatus.APPROVED
        # A captura continua lá, e o registro continua apontando para ela.
        assert registration.capture_object_key in world.storage.objects
        assert not [e for e in world.store.events if e.event_type == "CAPTURE_DELETED"]

    def test_capture_cannot_be_discarded_before_processing(self, world):
        registration = FaceRegistration.create(
            id=uuid.uuid4(),
            tenant_id=world.tenant.id,
            subject_id=uuid.uuid4(),
            capture_object_key="k",
            now=world.clock.now(),
        )
        with pytest.raises(InvalidStateTransition):
            registration.discard_capture(world.clock.now())


class TestFaceRegistrationQueries:
    async def test_quality_issues_are_recorded_on_the_registration(self, world):
        world.script.quality_ok = False
        accepted, status = await enroll(world)
        view = await world.get_face_registration().execute(
            world.tenant.id, accepted.registration_id
        )
        assert status is ProcessStatus.REJECTED
        assert view.decision is Decision.REJECTED and view.reason == "LOW_QUALITY"
        assert view.quality_issues and all(isinstance(i, str) for i in view.quality_issues)

    async def test_error_before_the_gate_has_no_quality(self, world):
        world.script.raise_on, world.script.exception = "detect_face", BiometricProviderError
        accepted, _ = await enroll(world)
        view = await world.get_face_registration().execute(
            world.tenant.id, accepted.registration_id
        )
        assert view.decision is Decision.ERROR and view.quality_issues is None

    async def test_verification_carries_quality_issues(self, world):
        await enroll(world)
        accepted = await verify(world)
        await world.process_verification().execute(accepted.verification_id)
        view = await world.get_verification().execute(world.tenant.id, accepted.verification_id)
        assert view.quality_issues == ()

    async def test_registration_of_other_tenant_is_not_found(self, world):
        accepted, _ = await enroll(world)
        with pytest.raises(FaceRegistrationNotFound):
            await world.get_face_registration().execute(uuid.uuid4(), accepted.registration_id)

    async def test_subject_face_tracks_active_and_latest(self, world):
        world.script.quality_ok = False
        first, _ = await enroll(world)
        view = await world.get_subject_face().execute(world.tenant.id, "user-1")
        assert not view.enrolled and view.latest_registration.registration_id == (
            first.registration_id
        )
        world.script.quality_ok = True
        world.clock.advance(minutes=1)
        second, _ = await enroll(world)
        view = await world.get_subject_face().execute(world.tenant.id, "user-1")
        assert view.enrolled and view.active_registration_id == second.registration_id
        assert view.latest_registration.registration_id == second.registration_id

    async def test_unknown_subject(self, world):
        with pytest.raises(SubjectNotFound):
            await world.get_subject_face().execute(world.tenant.id, "ghost")


async def reenroll(world, external_id: str = "user-1"):
    accepted = await world.register_face().execute(
        RegisterFaceCommand(
            tenant_id=world.tenant.id,
            external_subject_id=external_id,
            capture=capture(),
            replace=True,
        )
    )
    status = await world.process_registration().execute(accepted.registration_id)
    return accepted, status


class TestReenrollment:
    """Recadastro (ADR-008): PUT, confia no consumidor, template antigo apagado."""

    async def test_approved_replacement_supersedes_and_erases_old_template(self, world):
        old, _ = await enroll(world)
        world.clock.advance(minutes=1)
        new, status = await reenroll(world)

        assert status is ProcessStatus.APPROVED
        previous = world.store.registrations[old.registration_id]
        current = world.store.registrations[new.registration_id]
        assert previous.status is ProcessStatus.APPROVED  # histórico
        assert previous.template is None  # biometria apagada
        assert previous.superseded_by_id == new.registration_id
        assert previous.superseded_at == world.clock.now()
        assert not previous.is_active_reference and current.is_active_reference
        assert current.replaces_registration_id == old.registration_id

        view = await world.get_subject_face().execute(world.tenant.id, "user-1")
        assert view.active_registration_id == new.registration_id
        events = [e.event_type for e in world.store.events]
        assert "FACE_REGISTRATION_SUPERSEDED" in events

    async def test_rejected_replacement_keeps_current_reference(self, world):
        old, _ = await enroll(world)
        world.script.quality_ok = False
        new, status = await reenroll(world)

        assert status is ProcessStatus.REJECTED
        previous = world.store.registrations[old.registration_id]
        assert previous.is_active_reference and previous.template is not None
        assert previous.superseded_at is None
        assert "FACE_REGISTRATION_SUPERSEDED" not in [e.event_type for e in world.store.events]

    async def test_failed_replacement_keeps_current_reference(self, world):
        old, _ = await enroll(world)
        world.script.raise_on, world.script.exception = "generate_embedding", BiometricProviderError
        _, status = await reenroll(world)
        assert status is ProcessStatus.ERROR
        assert world.store.registrations[old.registration_id].is_active_reference

    async def test_requires_known_subject_with_active_reference(self, world):
        with pytest.raises(SubjectNotFound):
            await reenroll(world, "ghost")
        world.script.quality_ok = False
        await enroll(world, "user-2")  # rejeitado: sem referência ativa
        with pytest.raises(SubjectNotEnrolled):
            await reenroll(world, "user-2")
        assert world.queue.registrations and len(world.store.registrations) == 1

    async def test_post_still_refuses_enrolled_subject(self, world):
        await enroll(world)
        with pytest.raises(SubjectAlreadyEnrolled):
            await world.register_face().execute(
                RegisterFaceCommand(
                    tenant_id=world.tenant.id, external_subject_id="user-1", capture=capture()
                )
            )

    async def test_successive_replacements_last_approved_wins(self, world):
        first, _ = await enroll(world)
        world.clock.advance(minutes=1)
        second, _ = await reenroll(world)
        world.clock.advance(minutes=1)
        third, _ = await reenroll(world)

        regs = world.store.registrations
        assert regs[first.registration_id].superseded_by_id == second.registration_id
        assert regs[second.registration_id].superseded_by_id == third.registration_id
        assert [r.id for r in regs.values() if r.is_active_reference] == [third.registration_id]

    async def test_concurrent_initial_registration_still_fails(self, world):
        # Dois POST antes de qualquer processamento: o segundo a terminar não substitui.
        a = await world.register_face().execute(
            RegisterFaceCommand(
                tenant_id=world.tenant.id, external_subject_id="user-1", capture=capture()
            )
        )
        b = await world.register_face().execute(
            RegisterFaceCommand(
                tenant_id=world.tenant.id, external_subject_id="user-1", capture=capture()
            )
        )
        assert await world.process_registration().execute(a.registration_id) is (
            ProcessStatus.APPROVED
        )
        assert await world.process_registration().execute(b.registration_id) is (
            ProcessStatus.ERROR
        )
        assert world.store.registrations[a.registration_id].is_active_reference

    async def test_verification_after_replacement_uses_new_reference(self, world):
        await enroll(world)
        new, _ = await reenroll(world)
        accepted = await verify(world)
        await world.process_verification().execute(accepted.verification_id)
        verification = world.store.verifications[accepted.verification_id]
        assert verification.status is ProcessStatus.APPROVED
        assert verification.face_registration_id == new.registration_id


def submission(world, session_id, n=4):
    from app.application.dto import LivenessSubmission

    return LivenessSubmission(session_id=session_id, frames=tuple(capture() for _ in range(n)))


class TestActiveLivenessFlow:
    async def open(self, world, subject="user-1", purpose=None):
        from app.domain.entities import LivenessPurpose

        return await world.create_liveness_session().execute(
            tenant_id=world.tenant.id,
            external_subject_id=subject,
            purpose=purpose or LivenessPurpose.REGISTRATION,
        )

    async def register(self, world, liveness, subject="user-1"):
        return await world.register_face().execute(
            RegisterFaceCommand(
                tenant_id=world.tenant.id,
                external_subject_id=subject,
                capture=capture(),
                liveness=liveness,
            )
        )

    async def test_frames_stored_session_consumed_and_evidence_reaches_provider(self, world):
        session = await self.open(world)
        accepted = await self.register(world, submission(world, session.session_id))
        challenge = world.store.challenges[session.session_id]
        assert challenge.used_by_id == accepted.registration_id
        assert all(k in world.storage.objects for k in challenge.frame_keys)
        assert len(challenge.frame_keys) == 4

        await world.process_registration().execute(accepted.registration_id)
        (evidence,) = world.script.liveness_evidence
        assert evidence.session_reference == str(session.session_id)
        assert len(evidence.frames) == 4 and evidence.challenge == session.steps

    async def test_required_without_evidence_writes_nothing(self, world):
        world.liveness_intake = replace(world.liveness_intake, required=True)
        with pytest.raises(LivenessEvidenceRequired):
            await self.register(world, None)
        assert world.storage.objects == {} and world.store.registrations == {}

    @pytest.mark.parametrize("n", [2, 11])
    async def test_frame_count_bounds(self, world, n):
        session = await self.open(world)
        with pytest.raises(InvalidCapture):
            await self.register(world, submission(world, session.session_id, n))

    async def test_wrong_subject_or_purpose_or_reuse_is_refused(self, world):
        from app.domain.entities import LivenessPurpose

        other = await self.open(world, subject="user-2")
        with pytest.raises(LivenessSessionInvalid):
            await self.register(world, submission(world, other.session_id))
        verify_only = await self.open(world, purpose=LivenessPurpose.VERIFICATION)
        with pytest.raises(LivenessSessionInvalid):
            await self.register(world, submission(world, verify_only.session_id))
        ok = await self.open(world)
        await self.register(world, submission(world, ok.session_id))
        with pytest.raises(LivenessSessionInvalid):
            await self.register(world, submission(world, ok.session_id), subject="user-3")

    async def test_expired_session(self, world):
        session = await self.open(world)
        world.clock.advance(minutes=3)
        with pytest.raises(LivenessSessionInvalid):
            await self.register(world, submission(world, session.session_id))

    async def test_unknown_session(self, world):
        with pytest.raises(LivenessSessionNotFound):
            await self.register(world, submission(world, uuid.uuid4()))

    async def test_idempotent_replay_does_not_consume_another_session(self, world):
        from app.domain.entities import LivenessChallengeStatus, LivenessPurpose

        await enroll(world)
        first = await self.open(world, purpose=LivenessPurpose.VERIFICATION)
        second = await self.open(world, purpose=LivenessPurpose.VERIFICATION)
        command = dict(
            tenant_id=world.tenant.id,
            external_subject_id="user-1",
            idempotency_key="idem-live-0099",
            capture=capture(PNG, "image/png"),
        )
        a = await world.create_verification().execute(
            CreateVerificationCommand(**command, liveness=submission(world, first.session_id))
        )
        b = await world.create_verification().execute(
            CreateVerificationCommand(**command, liveness=submission(world, second.session_id))
        )
        assert a.verification_id == b.verification_id and b.replayed
        assert world.store.challenges[second.session_id].status is (LivenessChallengeStatus.CREATED)

    async def test_retention_deletes_frames_too(self, world):
        world.capture_retention = CaptureRetention.DELETE_AFTER_PROCESSING
        session = await self.open(world)
        accepted = await self.register(world, submission(world, session.session_id))
        await world.process_registration().execute(accepted.registration_id)
        assert world.storage.objects == {}
        deleted = [e for e in world.store.events if e.event_type == "CAPTURE_DELETED"]
        assert deleted[0].data["liveness_frames"] == 4
