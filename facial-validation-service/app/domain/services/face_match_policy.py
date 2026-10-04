"""Política de decisão de match 1:1, isolada, versionada e configurável.

Nenhum threshold vive fora desta política (nem nos adapters). O valor depende do
modelo de embedding e da calibração com dados reais (FAR/FRR); por isso NÃO há
threshold padrão: sem política configurada a validação termina em ERROR
(POLICY_NOT_CONFIGURED).

Referência (não é valor de produção): para opencv-sface/2021dec o candidato do
LFW é 0.3443 (FAR 0,10% / FRR 2,13%), status PENDING CALIBRATION.
Ver docs/biometric-poc-results.md e ADR-003.
"""

import math
from dataclasses import dataclass
from enum import StrEnum

from app.domain.exceptions import InvalidPolicy, PolicyModelMismatch


class MatchDecision(StrEnum):
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"


class CalibrationStatus(StrEnum):
    PENDING_CALIBRATION = "PENDING_CALIBRATION"
    CALIBRATED = "CALIBRATED"


class MatchPurpose(StrEnum):
    """Finalidades com políticas independentes (threshold e FAR podem diferir)."""

    SELFIE_VS_REFERENCE = "SELFIE_VS_REFERENCE"
    DOCUMENT_VS_SELFIE = "DOCUMENT_VS_SELFIE"  # PENDING INFORMATION: escopo ainda não definido


@dataclass(frozen=True, slots=True)
class FaceMatchPolicy:
    version: str
    model_name: str
    model_version: str
    # Similaridade mínima (maior = mais parecido) para considerar a mesma pessoa.
    min_similarity: float
    calibration_status: CalibrationStatus = CalibrationStatus.PENDING_CALIBRATION
    # FAR alvo definida pelo negócio (PENDING BUSINESS DECISION enquanto None).
    target_far: float | None = None
    purpose: MatchPurpose = MatchPurpose.SELFIE_VS_REFERENCE
    # De onde veio o valor: dataset, data, relatório de calibração.
    calibration_reference: str | None = None

    def __post_init__(self) -> None:
        if not self.version or not self.model_name or not self.model_version:
            raise InvalidPolicy("política exige version, model_name e model_version")
        if not math.isfinite(self.min_similarity):
            raise InvalidPolicy("min_similarity deve ser um número finito")
        if self.target_far is not None and not 0 < self.target_far < 1:
            raise InvalidPolicy("target_far deve estar entre 0 e 1")
        if self.calibration_status is CalibrationStatus.CALIBRATED and (
            self.target_far is None or not self.calibration_reference
        ):
            raise InvalidPolicy("política CALIBRATED exige target_far e calibration_reference")

    @property
    def is_calibrated(self) -> bool:
        return self.calibration_status is CalibrationStatus.CALIBRATED

    def applies_to(self, model_name: str, model_version: str) -> bool:
        return (self.model_name, self.model_version) == (model_name, model_version)

    def decide(self, similarity: float, *, model_name: str, model_version: str) -> MatchDecision:
        if not self.applies_to(model_name, model_version):
            raise PolicyModelMismatch(
                f"política {self.version} não se aplica ao modelo {model_name}:{model_version}"
            )
        if not math.isfinite(similarity):
            raise InvalidPolicy("similaridade inválida")
        return MatchDecision.MATCH if similarity >= self.min_similarity else MatchDecision.NO_MATCH


class FaceMatchPolicyRegistry:
    """Mantém as versões conhecidas; uma política ativa por finalidade.

    `allow_uncalibrated=False` (ambientes fora de local/test) recusa ativar uma
    política PENDING_CALIBRATION.
    """

    def __init__(self, *, allow_uncalibrated: bool = True) -> None:
        self._policies: dict[str, FaceMatchPolicy] = {}
        self._active: dict[MatchPurpose, str] = {}
        self._allow_uncalibrated = allow_uncalibrated

    def register(self, policy: FaceMatchPolicy, *, activate: bool = False) -> None:
        if policy.version in self._policies and self._policies[policy.version] != policy:
            raise InvalidPolicy(f"versão {policy.version} já registrada com outros parâmetros")
        if activate and not policy.is_calibrated and not self._allow_uncalibrated:
            raise InvalidPolicy(
                f"política {policy.version} está PENDING_CALIBRATION e não pode ser ativada "
                "neste ambiente"
            )
        self._policies[policy.version] = policy
        if activate:
            self._active[policy.purpose] = policy.version

    def get(self, version: str) -> FaceMatchPolicy | None:
        return self._policies.get(version)

    def active_for(self, purpose: MatchPurpose) -> FaceMatchPolicy | None:
        version = self._active.get(purpose)
        return self._policies.get(version) if version else None

    @property
    def active(self) -> FaceMatchPolicy | None:
        return self.active_for(MatchPurpose.SELFIE_VS_REFERENCE)
