"""Regressão do motor configurado no LFW, fold 1 (600 pares). Lento: só com -m evaluation.

É a prova de ponta a ponta com ROSTO REAL que os testes sintéticos não dão:
detector → alinhamento pelo DetectedFace → embedding → comparação, tudo pelos
adapters do serviço. Os limites abaixo são uma rede contra quebra da
integração (landmarks trocados, template corrompido, comparator errado),
generosos em relação ao POC (EER 1,47% nos 10 folds). **Não são calibração.**

Requer: python -m evaluation download-lfw e componentes BIOMETRIC_* configurados.
"""

import os
from pathlib import Path

import pytest

from app.config import get_settings
from evaluation import lfw
from evaluation.__main__ import evaluate_lfw

DATASET = Path(os.environ.get("EVALUATION_DATASET_DIR", "datasets/lfw"))

pytestmark = [
    pytest.mark.evaluation,
    pytest.mark.skipif(not lfw.is_available(DATASET), reason="LFW ausente (download-lfw)"),
    pytest.mark.skipif(
        "none" in (get_settings().biometric_detector, get_settings().biometric_embedder),
        reason="detector/embedder não configurados",
    ),
]


@pytest.fixture(scope="module")
def fold1():
    import asyncio

    report, rows = asyncio.run(evaluate_lfw(DATASET, [1], (), 0, progress=False))
    return report, rows


def test_detects_almost_every_lfw_image(fold1):
    report, _ = fold1
    assert report["dataset"]["failure_to_acquire"] <= 0.01 * report["dataset"]["images"]


def test_genuine_and_impostor_scores_are_separated(fold1):
    rec = fold1[0]["recognition"]
    assert rec["pairs_evaluated"]["genuine"] >= 295
    assert rec["genuine_score"]["mean"] - rec["impostor_score"]["mean"] > 0.3


def test_equal_error_rate_is_in_the_expected_range(fold1):
    eer = fold1[0]["recognition"]["eer"]
    assert eer["far"] <= 0.03 and eer["frr"] <= 0.03


def test_scores_csv_rows(fold1):
    _, rows = fold1
    assert len(rows) == 600
    assert all(row[0] == 1 for row in rows)
