"""Métricas e protocolo da suíte de avaliação, com dados sintéticos (sem dataset)."""

import numpy as np
import pytest

from app.domain.value_objects import BoundingBox, DetectedFace
from evaluation import lfw, metrics
from evaluation.runner import largest_face


class TestRates:
    def test_accept_when_score_reaches_threshold(self):
        point = metrics.rates(genuine=[0.2, 0.5, 0.9], impostor=[0.1, 0.5, 0.6], threshold=0.5)
        assert point.far == pytest.approx(2 / 3)  # 0.5 e 0.6 aceitos
        assert point.frr == pytest.approx(1 / 3)  # só 0.2 rejeitado

    def test_empty_scores_are_refused(self):
        with pytest.raises(ValueError):
            metrics.rates([], [0.1], 0.5)


class TestEqualErrorRate:
    def test_separable_scores_have_zero_eer(self):
        point = metrics.equal_error_rate(genuine=[0.7, 0.8, 0.9], impostor=[0.1, 0.2, 0.3])
        assert point.far == 0 and point.frr == 0
        assert 0.3 < point.threshold <= 0.7

    def test_overlapping_gaussians(self):
        rng = np.random.default_rng(0)
        genuine, impostor = rng.normal(1, 0.5, 20_000), rng.normal(0, 0.5, 20_000)
        point = metrics.equal_error_rate(genuine, impostor)
        # Normais com médias 0 e 1 e σ = 0,5: EER teórica = Φ(-1) ≈ 15,87%, threshold 0,5.
        assert point.far == pytest.approx(0.1587, abs=0.01)
        assert point.frr == pytest.approx(0.1587, abs=0.01)
        assert point.threshold == pytest.approx(0.5, abs=0.02)


class TestThresholdAtFar:
    def test_lowest_threshold_meeting_target(self):
        impostor = np.linspace(0, 0.99, 100)  # 100 impostores
        genuine = np.linspace(0.5, 1.0, 50)
        point = metrics.threshold_at_far(genuine, impostor, 0.05)
        assert point.far <= 0.05
        assert 0.94 < point.threshold <= 0.95  # pode cair num score genuíno entre impostores
        assert metrics.rates(genuine, impostor, 0.94).far > 0.05  # impostor logo abaixo

    def test_zero_far_is_always_reachable(self):
        point = metrics.threshold_at_far([0.5], [0.4, 0.9], 0.0)
        assert point.far == 0 and point.threshold > 0.9


class TestTenFold:
    def test_perfectly_separable(self):
        folds = np.repeat(np.arange(10), 4)
        genuine = np.tile([True, True, False, False], 10)
        scores = np.where(genuine, 0.8, 0.1)
        mean, std, per_fold = metrics.ten_fold_accuracy(folds, genuine, scores)
        assert mean == 1.0 and std == 0.0 and len(per_fold) == 10

    def test_threshold_comes_from_the_other_folds(self):
        # No fold 0 os scores estão invertidos: o threshold aprendido nos outros 9 erra tudo.
        folds = np.repeat(np.arange(10), 2)
        genuine = np.tile([True, False], 10)
        scores = np.where(genuine, 0.8, 0.1)
        scores[0], scores[1] = 0.1, 0.8
        _, _, per_fold = metrics.ten_fold_accuracy(folds, genuine, scores)
        assert per_fold[0] == 0.0 and all(a == 1.0 for a in per_fold[1:])


class TestSummaries:
    def test_summarize_and_histogram(self):
        summary = metrics.summarize([0.0, 0.5, 1.0])
        assert summary["count"] == 3 and summary["mean"] == pytest.approx(0.5)
        hist = metrics.histogram([0.0, 0.5, 0.95], bins=2, value_range=(0, 1))
        assert hist["counts"] == [1, 2]


class TestLfwProtocol:
    PAIRS = "2\t2\nAna\t1\t2\nBia\t1\t3\nAna\t1\tBia\t2\nCai\t4\tDan\t1\n" + (
        "Eva\t1\t2\nFlo\t2\t3\nGil\t1\tHal\t1\nIgo\t1\tJu\t2\n"
    )

    def test_parse_view2_pairs(self):
        pairs = lfw.parse_pairs(self.PAIRS)
        assert len(pairs) == 8
        assert [p.fold for p in pairs] == [0, 0, 0, 0, 1, 1, 1, 1]
        assert [p.genuine for p in pairs[:4]] == [True, True, False, False]
        assert pairs[0].a.relative_path == "lfw/Ana/Ana_0001.jpg"
        assert pairs[2].b == lfw.ImageRef("Bia", 2)

    def test_count_must_match_header(self):
        with pytest.raises(ValueError):
            lfw.parse_pairs("2\t2\nAna\t1\t2\n")

    def test_largest_face_is_chosen(self):
        small = DetectedFace(box=BoundingBox(0, 0, 20, 20))
        large = DetectedFace(box=BoundingBox(50, 50, 120, 140))
        assert largest_face((small, large)) is large
        assert largest_face(()) is None
