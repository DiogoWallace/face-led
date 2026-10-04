from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Header, Path, Response, UploadFile, status

from app.application.dto import CreateVerificationCommand
from app.interfaces.http.dependencies import (
    ContainerDep,
    CurrentTenant,
    LivenessFramesField,
    LivenessSessionField,
    read_capture,
    read_liveness,
)
from app.interfaces.http.schemas import (
    ErrorResponse,
    ExternalSubjectId,
    IdempotencyKey,
    VerificationResponse,
)
from app.interfaces.http.serializers import verification_response

router = APIRouter(prefix="/api/v1", tags=["verifications"])

_ERRORS = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
}


@router.post(
    "/subjects/{subject_id}/verifications",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=VerificationResponse,
    responses={**_ERRORS, 422: {"model": ErrorResponse}},
    summary="Cria uma validação facial 1:1 contra o cadastro do subject",
)
async def create_verification(
    subject_id: Annotated[ExternalSubjectId, Path()],
    image: Annotated[UploadFile, File(description="Captura JPEG ou PNG")],
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
    tenant: CurrentTenant,
    container: ContainerDep,
    response: Response,
    liveness_session_id: LivenessSessionField = None,
    frames: LivenessFramesField = None,
) -> VerificationResponse:
    capture = await read_capture(image, container.settings.max_capture_bytes)
    liveness = await read_liveness(
        liveness_session_id, frames, container.settings.liveness_frame_max_bytes
    )
    view = await container.create_verification().execute(
        CreateVerificationCommand(
            tenant_id=tenant.id,
            external_subject_id=subject_id,
            idempotency_key=idempotency_key,
            capture=capture,
            liveness=liveness,
        )
    )
    if view.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return verification_response(view)


@router.get(
    "/verifications/{verification_id}",
    response_model=VerificationResponse,
    responses=_ERRORS,
    summary="Consulta o resultado de uma validação",
)
async def get_verification(
    verification_id: UUID, tenant: CurrentTenant, container: ContainerDep
) -> VerificationResponse:
    view = await container.get_verification().execute(tenant.id, verification_id)
    return verification_response(view)
