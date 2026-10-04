"""Cenários biométricos REAIS - estrutura preparada, ainda não executável.

Estes testes só fazem sentido com um motor biométrico escolhido (ADR-003) e um
dataset de avaliação com consentimento (fora do Git). Até lá ficam como SKIP:
nenhum resultado aqui é simulado ou fingido.

Para ativar no futuro:
  BIOMETRIC_DETECTOR=<motor> BIOMETRIC_EMBEDDER=<motor> \
  BIOMETRIC_DATASET_DIR=/caminho/dataset pytest -m biometric
"""

import os

import pytest

pytestmark = [
    pytest.mark.biometric,
    pytest.mark.skipif(
        os.environ.get("BIOMETRIC_EMBEDDER", "none") == "none"
        or not os.environ.get("BIOMETRIC_DATASET_DIR"),
        reason="motor biométrico e dataset de avaliação ainda não definidos (ADR-003)",
    ),
]

SCENARIOS = {
    "no_face": "imagem sem rosto -> REJECTED/NO_FACE",
    "multiple_faces": "imagem com mais de um rosto -> REJECTED/MULTIPLE_FACES",
    "poor_quality": "imagem desfocada/escura/baixa resolução -> REJECTED/LOW_QUALITY",
    "different_person": "captura de outra pessoa -> REJECTED/FACE_MISMATCH",
    "same_person": "nova captura da mesma pessoa -> APPROVED",
    "liveness_live": "pessoa real diante da câmera -> liveness LIVE",
    "liveness_spoof": "foto de foto / tela / máscara -> REJECTED/LIVENESS_FAILED",
}


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_scenario(scenario: str) -> None:
    pytest.fail(f"cenário '{scenario}' ainda não implementado: {SCENARIOS[scenario]}")
