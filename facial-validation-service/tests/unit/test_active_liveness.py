"""Regra do liveness ativo (ADR-010) com sequências SINTÉTICAS de medidas por quadro.

Cada cenário imita o que o YuNet mediria: giro (nariz vs. olhos) e distância
entre os olhos. Não prova taxa de acerto com gente real — isso exige dataset
(APCER/BPCER, PENDING) —, prova que a regra faz o que promete.
"""

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.entities import LivenessChallenge, LivenessChallengeStatus, LivenessPurpose
from app.domain.exceptions import InvalidPolicy, LivenessSessionInvalid
from app.domain.services import (
    ActiveLivenessParameters,
    ChallengeStep,
    FrameObservation,
    new_challenge,
    observe,
    verify_challenge,
)
from app.domain.value_objects import (
    BoundingBox,
    DetectedFace,
    FaceLandmarks,
    LivenessVerdict,
    Point,
)

P = ActiveLivenessParameters()
L, R, C = ChallengeStep.TURN_LEFT, ChallengeStep.TURN_RIGHT, ChallengeStep.MOVE_CLOSER


def frame(yaw: float, eyes: float = 60.0) -> FrameObservation:
    return FrameObservation(face_count=1, yaw=yaw, eye_distance=eyes)


def ramp(a: float, b: float, n: int, eyes_a: float = 60.0, eyes_b: float = 60.0):
    return [
        frame(a + (b - a) * k / (n - 1), eyes_a + (eyes_b - eyes_a) * k / (n - 1)) for k in range(n)
    ]


def real_person(*steps: ChallengeStep) -> list[FrameObservation]:
    """Movimento contínuo: frontal → passo → volta ao frontal → próximo passo."""
    frames = ramp(0.0, 0.0, 3)
    for step in steps:
        if step is C:
            frames += ramp(0.0, 0.0, 4, 60, 85) + ramp(0.0, 0.0, 3, 85, 60)
        else:
            peak = 0.5 if step is L else -0.5
            # Giro real: olhos encolhem pouco (cos 30° ≈ 0,87).
            frames += ramp(0.0, peak, 4, 60, 52) + ramp(peak, 0.0, 4, 52, 60)
    return frames


class TestRealPerson:
    @pytest.mark.parametrize("steps", [(L, R, C), (R, C, L), (C, L, R), (L, R), (R, L, C)])
    def test_completed_challenge_is_live(self, steps):
        outcome = verify_challenge(real_person(*steps), steps, P)
        assert outcome.verdict is LivenessVerdict.LIVE, outcome.detail
        assert len(outcome.checkpoints) == len(steps) + 1

    def test_one_dropped_frame_is_tolerated(self):
        frames = real_person(L, R)
        frames[5] = FrameObservation(face_count=0)
        assert verify_challenge(frames, (L, R), P).verdict is LivenessVerdict.LIVE


class TestAttacks:
    def test_still_photo_never_turns(self):
        frames = [frame(0.02) for _ in range(20)]
        outcome = verify_challenge(frames, (L, R, C), P)
        assert outcome.verdict is LivenessVerdict.SPOOF
        assert outcome.detail == "STEP_1_TURN_LEFT_NOT_DONE"

    def test_tilted_flat_photo_shrinks_eyes_too_much(self):
        # Medido no LFW (ADR-010): foto plana a 65° chega a giro 0,36 com olhos a 51%.
        frames = ramp(0.0, 0.36, 8, 60, 30) + ramp(0.36, 0.0, 8, 30, 60)
        outcome = verify_challenge(frames, (L,), P)
        assert outcome.verdict is LivenessVerdict.SPOOF

    def test_swapping_photos_jumps_pose(self):
        # Foto frontal da vítima, depois foto dela virada: sem poses intermediárias.
        frames = [frame(0.0)] * 6 + [frame(0.55, 55)] * 6 + [frame(0.0)] * 4
        outcome = verify_challenge(frames, (L,), P)
        assert outcome.verdict is LivenessVerdict.SPOOF and outcome.detail == "POSE_JUMP"

    def test_removing_photo_from_view_loses_tracking(self):
        # Some de quadro por 2 quadros seguidos no meio do desafio (rastreamento geral 90%).
        frames = ramp(0.0, 0.0, 8) + [FrameObservation(face_count=0)] * 2 + ramp(0.5, 0.0, 10)
        outcome = verify_challenge(frames, (L,), P)
        assert outcome.verdict is LivenessVerdict.SPOOF and outcome.detail == "TRACKING_LOST"

    def test_replayed_video_with_other_order_fails(self):
        recorded = real_person(L, R, C)
        outcome = verify_challenge(recorded, (R, L, C), P)
        assert outcome.verdict is LivenessVerdict.SPOOF

    def test_abrupt_zoom_jumps_scale(self):
        frames = ramp(0.0, 0.0, 5) + [frame(0.0, 90)] * 5
        outcome = verify_challenge(frames, (C,), P)
        assert outcome.verdict is LivenessVerdict.SPOOF and outcome.detail == "SCALE_JUMP"


class TestInconclusive:
    def test_too_few_frames(self):
        assert verify_challenge([frame(0.0)] * 3, (L,), P).detail == "TOO_FEW_FRAMES"

    def test_face_not_tracked(self):
        frames = [FrameObservation(face_count=2)] * 6 + [frame(0.0)] * 4
        outcome = verify_challenge(frames, (L,), P)
        assert outcome.verdict is LivenessVerdict.INCONCLUSIVE

    def test_no_frontal_start_is_spoof(self):
        frames = ramp(0.4, 0.6, 10)
        assert verify_challenge(frames, (L,), P).detail == "NO_FRONTAL_START"


class TestChallengeGeneration:
    def test_random_valid_sequences(self):
        rng = random.Random(7)  # noqa: S311 - semente fixa só para o teste ser reprodutível
        seen = set()
        for _ in range(500):
            steps = new_challenge(3, rng)  # type: ignore[arg-type]
            assert len(steps) == 3 and steps.count(C) <= 1
            assert all(a is not b for a, b in zip(steps, steps[1:], strict=False))
            assert L in steps or R in steps
            seen.add(steps)
        assert len(seen) >= 8  # o servidor não repete sempre a mesma ordem

    @pytest.mark.parametrize("length", [1, 5])
    def test_length_bounds(self, length):
        with pytest.raises(InvalidPolicy):
            new_challenge(length)


class TestObservation:
    def face(self, nose_x: float) -> DetectedFace:
        return DetectedFace(
            box=BoundingBox(0, 0, 100, 100),
            landmarks=FaceLandmarks(
                right_eye=Point(30, 40),
                left_eye=Point(70, 40),
                nose_tip=Point(nose_x, 60),
                mouth_right=Point(35, 80),
                mouth_left=Point(65, 80),
            ),
        )

    def test_yaw_sign_follows_unmirrored_convention(self):
        # Pessoa vira para a ESQUERDA dela → nariz vai para x maior na imagem.
        assert observe([self.face(70)]).yaw == pytest.approx(0.5)
        assert observe([self.face(30)]).yaw == pytest.approx(-0.5)
        assert observe([self.face(50)]).eye_distance == pytest.approx(40)

    def test_zero_or_many_faces_are_not_tracked(self):
        assert not observe([]).tracked
        assert not observe([self.face(50), self.face(50)]).tracked


class TestChallengeSession:
    def challenge(self, now):
        return LivenessChallenge.create(
            id=uuid4(),
            tenant_id=uuid4(),
            external_subject_id="user-1",
            purpose=LivenessPurpose.REGISTRATION,
            steps=("TURN_LEFT", "TURN_RIGHT"),
            ttl=timedelta(minutes=2),
            now=now,
        )

    def test_single_use(self):
        now = datetime(2026, 1, 1, tzinfo=UTC)
        c = self.challenge(now)
        c.consume(
            external_subject_id="user-1",
            purpose=LivenessPurpose.REGISTRATION,
            by_id=uuid4(),
            frame_keys=("k1",),
            now=now,
        )
        assert c.status is LivenessChallengeStatus.USED and c.frame_keys == ("k1",)
        with pytest.raises(LivenessSessionInvalid):
            c.consume(
                external_subject_id="user-1",
                purpose=LivenessPurpose.REGISTRATION,
                by_id=uuid4(),
                frame_keys=(),
                now=now,
            )

    @pytest.mark.parametrize(
        ("subject", "purpose", "delay"),
        [
            ("user-2", LivenessPurpose.REGISTRATION, 0),
            ("user-1", LivenessPurpose.VERIFICATION, 0),
            ("user-1", LivenessPurpose.REGISTRATION, 121),
        ],
        ids=["outro-subject", "outra-finalidade", "expirada"],
    )
    def test_bound_to_subject_purpose_and_time(self, subject, purpose, delay):
        now = datetime(2026, 1, 1, tzinfo=UTC)
        c = self.challenge(now)
        with pytest.raises(LivenessSessionInvalid):
            c.consume(
                external_subject_id=subject,
                purpose=purpose,
                by_id=uuid4(),
                frame_keys=(),
                now=now + timedelta(seconds=delay),
            )


class TestWorkerFrameObservation:
    """Job da captura guiada (/dev/liveness): só medidas e caixa normalizada, nunca imagem."""

    async def test_returns_measurements_and_normalized_box(self):
        from types import SimpleNamespace

        from app.worker import observe_liveness_frame
        from tests.fakes import BiometricScript, ScriptedDetector

        script = BiometricScript()
        container = SimpleNamespace(
            pipeline=SimpleNamespace(components=SimpleNamespace(detector=ScriptedDetector(script)))
        )
        result = await observe_liveness_frame({"container": container}, b"jpeg", "image/jpeg")
        assert result["face_count"] == 1 and result["image_width"] == 640
        assert result["box"] == [0.0, 0.0, round(100 / 640, 4), round(100 / 480, 4)]
        assert set(result) == {
            "face_count",
            "yaw",
            "eye_distance",
            "image_width",
            "image_height",
            "box",
        }

    async def test_no_box_without_exactly_one_face(self):
        from types import SimpleNamespace

        from app.worker import observe_liveness_frame
        from tests.fakes import BiometricScript, ScriptedDetector

        script = BiometricScript(face_count=2)
        container = SimpleNamespace(
            pipeline=SimpleNamespace(components=SimpleNamespace(detector=ScriptedDetector(script)))
        )
        result = await observe_liveness_frame({"container": container}, b"jpeg", "image/jpeg")
        assert result["face_count"] == 2 and result["box"] is None and result["yaw"] is None
