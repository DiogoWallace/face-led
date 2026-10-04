from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Path, UploadFile, status

from app.application.dto import RegisterFaceCommand
from app.container import Container
from app.domain.entities import Tenant
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
    FaceRegistrationAcceptedResponse,
    FaceRegistrationResponse,
    SubjectFaceResponse,
)
from app.interfaces.http.serializers import face_registration_response

router = APIRouter(prefix="/api/v1", tags=["faces"])

_READ_ERRORS = {401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}}


@router.post(
    "/subjects/{subject_id}/face",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=FaceRegistrationAcceptedResponse,
    responses={
        401: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Cadastro facial inicial (referência biométrica)",
)
async def register_face(
    subject_id: Annotated[ExternalSubjectId, Path()],
    image: Annotated[UploadFile, File(description="Captura JPEG ou PNG")],
    tenant: CurrentTenant,
    container: ContainerDep,
    liveness_session_id: LivenessSessionField = None,
    frames: LivenessFramesField = None,
) -> FaceRegistrationAcceptedResponse:
    return await _register(
        subject_id, image, tenant, container, liveness_session_id, frames, replace=False
    )


@router.put(
    "/subjects/{subject_id}/face",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=FaceRegistrationAcceptedResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Recadastro: substitui a referência ativa do subject",
    description=(
        "Processa o novo envio como um cadastro. A referência atual só é substituída se o "
        "novo envio for APROVADO; recusado ou com erro, a atual continua valendo. O template "
        "substituído é apagado. Exige referência ativa (409 SUBJECT_NOT_ENROLLED; o primeiro "
        "cadastro é pelo POST). Ver ADR-008."
    ),
)
async def replace_face(
    subject_id: Annotated[ExternalSubjectId, Path()],
    image: Annotated[UploadFile, File(description="Captura JPEG ou PNG")],
    tenant: CurrentTenant,
    container: ContainerDep,
    liveness_session_id: LivenessSessionField = None,
    frames: LivenessFramesField = None,
) -> FaceRegistrationAcceptedResponse:
    return await _register(
        subject_id, image, tenant, container, liveness_session_id, frames, replace=True
    )


async def _register(
    subject_id: str,
    image: UploadFile,
    tenant: Tenant,
    container: Container,
    liveness_session_id: UUID | None,
    frames: list[UploadFile] | None,
    *,
    replace: bool,
) -> FaceRegistrationAcceptedResponse:
    capture = await read_capture(image, container.settings.max_capture_bytes)
    liveness = await read_liveness(
        liveness_session_id, frames, container.settings.liveness_frame_max_bytes
    )
    accepted = await container.register_face().execute(
        RegisterFaceCommand(
            tenant_id=tenant.id,
            external_subject_id=subject_id,
            capture=capture,
            replace=replace,
            liveness=liveness,
        )
    )
    return FaceRegistrationAcceptedResponse(
        registration_id=accepted.registration_id,
        subject_id=accepted.external_subject_id,
        status=accepted.status,
    )


@router.get(
    "/face-registrations/{registration_id}",
    response_model=FaceRegistrationResponse,
    responses=_READ_ERRORS,
    summary="Consulta o resultado de um envio de cadastro facial",
)
async def get_face_registration(
    registration_id: UUID, tenant: CurrentTenant, container: ContainerDep
) -> FaceRegistrationResponse:
    view = await container.get_face_registration().execute(tenant.id, registration_id)
    return face_registration_response(view)


@router.get(
    "/subjects/{subject_id}/face",
    response_model=SubjectFaceResponse,
    responses=_READ_ERRORS,
    summary="Estado do cadastro facial do subject (referência ativa e último envio)",
)
async def get_subject_face(
    subject_id: Annotated[ExternalSubjectId, Path()],
    tenant: CurrentTenant,
    container: ContainerDep,
) -> SubjectFaceResponse:
    view = await container.get_subject_face().execute(tenant.id, subject_id)
    latest = view.latest_registration
    return SubjectFaceResponse(
        subject_id=view.external_subject_id,
        enrolled=view.enrolled,
        active_registration_id=view.active_registration_id,
        latest_registration=face_registration_response(latest) if latest else None,
    )
