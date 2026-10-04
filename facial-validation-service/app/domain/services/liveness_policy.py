"""Exigência de liveness no pipeline.

REQUIRED (padrão): sem LivenessProvider real o processo termina em
ERROR/LIVENESS_NOT_CONFIGURED; nunca aprova sem prova de vida.
DISABLED_FOR_EVALUATION: pula o liveness para medir só o reconhecimento facial.
Aceito apenas em ambientes local/test (a composição recusa nos demais).
"""

from enum import StrEnum

from app.domain.exceptions import InvalidPolicy


class LivenessRequirement(StrEnum):
    REQUIRED = "REQUIRED"
    DISABLED_FOR_EVALUATION = "DISABLED_FOR_EVALUATION"


def ensure_liveness_requirement_allowed(
    requirement: LivenessRequirement, *, evaluation_environment: bool
) -> None:
    if requirement is LivenessRequirement.DISABLED_FOR_EVALUATION and not evaluation_environment:
        raise InvalidPolicy(
            "LIVENESS_REQUIREMENT=DISABLED_FOR_EVALUATION só é aceito em local/test"
        )
