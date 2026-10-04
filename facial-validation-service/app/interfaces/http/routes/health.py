import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.interfaces.http.dependencies import ContainerDep
from app.interfaces.http.schemas import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

_CHECK_TIMEOUT_SECONDS = 3


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness do processo: não consulta dependências."""
    return HealthResponse(status="ok")


@router.get(
    "/ready", response_model=ReadinessResponse, responses={503: {"model": ReadinessResponse}}
)
async def ready(container: ContainerDep) -> JSONResponse:
    """Readiness: PostgreSQL, Redis e storage precisam responder."""
    checks: dict[str, str] = {}
    for name, check in container.readiness_checks.items():
        try:
            await asyncio.wait_for(check(), timeout=_CHECK_TIMEOUT_SECONDS)
            checks[name] = "ok"
        except Exception as error:
            logger.warning(
                "readiness_check_failed", extra={"check": name, "error_type": type(error).__name__}
            )
            checks[name] = "fail"
    ok = all(value == "ok" for value in checks.values())
    body = ReadinessResponse(status="ok" if ok else "fail", checks=checks)
    return JSONResponse(status_code=200 if ok else 503, content=body.model_dump())
