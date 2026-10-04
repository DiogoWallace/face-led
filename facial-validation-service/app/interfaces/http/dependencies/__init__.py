from typing import Annotated
from uuid import UUID

from fastapi import Depends, File, Form, Header, Request, UploadFile

from app.application.dto import LivenessSubmission
from app.container import Container
from app.domain.entities import Tenant
from app.domain.exceptions import InvalidCapture
from app.domain.value_objects import CaptureData


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_current_tenant(
    container: ContainerDep,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Tenant:
    return await container.authenticate_tenant().execute(x_api_key)


CurrentTenant = Annotated[Tenant, Depends(get_current_tenant)]


async def read_liveness(
    session_id: UUID | None, frames: list[UploadFile] | None, max_bytes: int
) -> LivenessSubmission | None:
    """Monta a evidência do liveness ativo. Sessão sem quadros (ou o contrário) é erro."""
    if session_id is None and not frames:
        return None
    if session_id is None or not frames:
        raise InvalidCapture("envie liveness_session_id e os quadros (frames) juntos")
    return LivenessSubmission(
        session_id=session_id,
        frames=tuple([await read_capture(frame, max_bytes) for frame in frames]),
    )


async def read_capture(image: UploadFile, max_bytes: int) -> CaptureData:
    # Lê no máximo max_bytes + 1 para rejeitar arquivos grandes sem carregá-los inteiros.
    content = await image.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise InvalidCapture("arquivo excede o tamanho máximo")
    return CaptureData(content=content, content_type=(image.content_type or "").lower())


# Liveness ativo (ADR-010): opcionais no contrato; exigidos quando LIVENESS_PROVIDER=active.
LivenessSessionField = Annotated[
    UUID | None, Form(description="Sessão de POST /api/v1/liveness-sessions")
]
LivenessFramesField = Annotated[
    list[UploadFile] | None,
    File(description="Quadros JPEG/PNG capturados durante o desafio, em ordem, sem espelhar"),
]
