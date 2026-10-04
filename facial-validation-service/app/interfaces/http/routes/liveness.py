from fastapi import APIRouter, status

from app.domain.entities import LivenessPurpose
from app.interfaces.http.dependencies import ContainerDep, CurrentTenant
from app.interfaces.http.schemas import (
    ErrorResponse,
    LivenessFramesSpec,
    LivenessSessionRequest,
    LivenessSessionResponse,
)

router = APIRouter(prefix="/api/v1", tags=["liveness"])

INSTRUCTIONS = {
    "TURN_LEFT": "Vire o rosto devagar para a sua esquerda e volte ao centro",
    "TURN_RIGHT": "Vire o rosto devagar para a sua direita e volte ao centro",
    "MOVE_CLOSER": "Aproxime o rosto da câmera devagar e volte",
}


@router.post(
    "/liveness-sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=LivenessSessionResponse,
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Abre uma sessão de prova de vida (desafio sorteado, uso único)",
    description=(
        "Liveness ativo próprio (ADR-010). O servidor sorteia os passos; o front mostra as "
        "instruções, captura os quadros e envia `liveness_session_id` + `frames` no cadastro, "
        "recadastro ou validação do mesmo subject. A sessão vale uma vez e expira."
    ),
)
async def create_liveness_session(
    body: LivenessSessionRequest, tenant: CurrentTenant, container: ContainerDep
) -> LivenessSessionResponse:
    view = await container.create_liveness_session().execute(
        tenant_id=tenant.id,
        external_subject_id=body.subject_id,
        purpose=LivenessPurpose(body.purpose),
    )
    return LivenessSessionResponse(
        session_id=view.session_id,
        subject_id=view.external_subject_id,
        purpose=view.purpose,
        challenge=list(view.steps),
        instructions=[INSTRUCTIONS[s] for s in view.steps],
        expires_at=view.expires_at,
        frames=LivenessFramesSpec(
            min=view.min_frames, max=view.max_frames, max_bytes_each=view.frame_max_bytes
        ),
    )
