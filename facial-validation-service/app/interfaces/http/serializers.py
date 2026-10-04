"""Serialização do resultado — usada pelo GET e pelo corpo do webhook.

Um único caminho garante que o webhook é "igual ao GET" (decisão do usuário,
ADR-009) e que as regras do ADR-007 valem nos dois: sem score, qualidade só em
códigos.
"""

from typing import Any

from app.application.dto import FaceRegistrationView, VerificationView
from app.interfaces.http.schemas import (
    FaceRegistrationResponse,
    QualityResponse,
    VerificationResponse,
)


def face_registration_response(view: FaceRegistrationView) -> FaceRegistrationResponse:
    return FaceRegistrationResponse(
        registration_id=view.registration_id,
        subject_id=view.external_subject_id,
        status=view.status,
        decision=view.decision,
        reason=view.reason,
        quality=QualityResponse.from_issues(view.quality_issues),
        replaces_registration_id=view.replaces_registration_id,
        superseded_at=view.superseded_at,
        superseded_by_id=view.superseded_by_id,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def verification_response(view: VerificationView) -> VerificationResponse:
    return VerificationResponse(
        verification_id=view.verification_id,
        subject_id=view.external_subject_id,
        status=view.status,
        decision=view.decision,
        reason=view.reason,
        policy_version=view.policy_version,
        quality=QualityResponse.from_issues(view.quality_issues),
        created_at=view.created_at,
        updated_at=view.updated_at,
        expires_at=view.expires_at,
    )


class HttpResultRenderer:
    """Implementa a porta ResultRenderer com os mesmos modelos de resposta da API."""

    def face_registration(self, view: FaceRegistrationView) -> dict[str, Any]:
        return face_registration_response(view).model_dump(mode="json")

    def verification(self, view: VerificationView) -> dict[str, Any]:
        return verification_response(view).model_dump(mode="json")
