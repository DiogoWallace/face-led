from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.domain.value_objects import Decision, ProcessStatus

# external_id do sistema consumidor. Pode ser dado pessoal: nunca vai para logs ou chaves S3.
ExternalSubjectId = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:\-]+$")
]
IdempotencyKey = Annotated[
    str, StringConstraints(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:\-]+$")
]


class QualityResponse(BaseModel):
    """Resultado do QualityGate. Só códigos: medidas numéricas não são expostas."""

    passed: bool
    issues: list[str] = Field(
        description=(
            "Códigos do que impediu a captura: NO_FACE, MULTIPLE_FACES, FACE_TOO_SMALL, "
            "FACE_TOO_SMALL_RELATIVE, BLURRY, TOO_DARK, TOO_BRIGHT, SCORE_BELOW_MINIMUM:<medidor>. "
            "Vazio quando passou."
        )
    )

    @classmethod
    def from_issues(cls, issues: tuple[str, ...] | None) -> "QualityResponse | None":
        return None if issues is None else cls(passed=not issues, issues=list(issues))


_QUALITY_DESCRIPTION = (
    "Nulo quando a qualidade não chegou a ser avaliada (ainda em andamento, ou falha antes)."
)


class FaceRegistrationAcceptedResponse(BaseModel):
    registration_id: UUID
    subject_id: str
    status: ProcessStatus


class VerificationResponse(BaseModel):
    verification_id: UUID
    subject_id: str
    status: ProcessStatus
    decision: Decision | None = Field(
        description="APPROVED, REJECTED ou ERROR (inconclusivo). Nulo enquanto em andamento."
    )
    reason: str | None
    policy_version: str | None
    quality: QualityResponse | None = Field(description=_QUALITY_DESCRIPTION)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class FaceRegistrationResponse(BaseModel):
    registration_id: UUID
    subject_id: str
    status: ProcessStatus
    decision: Decision | None = Field(
        description="APPROVED, REJECTED ou ERROR (inconclusivo). Nulo enquanto em andamento."
    )
    reason: str | None
    quality: QualityResponse | None = Field(description=_QUALITY_DESCRIPTION)
    replaces_registration_id: UUID | None = Field(
        description="Preenchido em recadastro (PUT): a referência que este envio substitui."
    )
    superseded_at: datetime | None = Field(
        description="Quando esta referência foi substituída por um recadastro aprovado."
    )
    superseded_by_id: UUID | None
    created_at: datetime
    updated_at: datetime


class SubjectFaceResponse(BaseModel):
    subject_id: str
    enrolled: bool = Field(description="Há referência biométrica ativa (cadastro APPROVED).")
    active_registration_id: UUID | None
    latest_registration: FaceRegistrationResponse | None = Field(
        description="Envio de cadastro mais recente, qualquer que seja o resultado."
    )


class LivenessSessionRequest(BaseModel):
    subject_id: ExternalSubjectId
    purpose: Literal["REGISTRATION", "VERIFICATION"] = Field(
        description="REGISTRATION para cadastro e recadastro; VERIFICATION para validação."
    )


class LivenessFramesSpec(BaseModel):
    min: int
    max: int
    max_bytes_each: int
    content_types: list[str] = ["image/jpeg", "image/png"]


class LivenessSessionResponse(BaseModel):
    session_id: UUID
    subject_id: str
    purpose: str
    challenge: list[str] = Field(
        description=(
            "Passos, na ordem: TURN_LEFT (virar para a esquerda DA PESSOA), TURN_RIGHT, "
            "MOVE_CLOSER. Comece olhando de frente, volte ao centro entre os passos."
        )
    )
    instructions: list[str] = Field(description="Texto sugerido para cada passo, em PT-BR.")
    expires_at: datetime
    frames: LivenessFramesSpec = Field(
        description=(
            "Quadros a capturar durante o desafio, SEM espelhamento, em ordem, ~5 por segundo. "
            "Envie-os no campo `frames` junto da selfie e de `liveness_session_id`."
        )
    )


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: str


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, str]
