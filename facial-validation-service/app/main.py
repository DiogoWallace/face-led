import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.config import Settings, get_settings
from app.container import Container, build_container
from app.infrastructure.observability.logging import configure_logging, request_id_var
from app.infrastructure.observability.tracing import configure_tracing
from app.interfaces.http.errors import register_error_handlers
from app.interfaces.http.routes import dev, faces, health, liveness, verifications

logger = logging.getLogger("app.http")

_REQUEST_ID = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
ContainerBuilder = Callable[[Settings], Awaitable[Container]]


def create_app(
    settings: Settings | None = None, container_builder: ContainerBuilder = build_container
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = await container_builder(settings)
        logger.info(
            "startup",
            extra={
                "env": settings.app_env,
                "detector": settings.biometric_detector,
                "embedder": settings.biometric_embedder,
                "liveness": settings.liveness_provider,
            },
        )
        try:
            yield
        finally:
            await app.state.container.close()

    app = FastAPI(
        title="Facial Validation Service",
        version="0.1.0",
        description="Cadastro facial e validação 1:1 com prova de vida.",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID.match(incoming) else str(uuid.uuid4())
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        # Só método, rota e status: nunca corpo, query ou headers de autenticação.
        route = request.scope.get("route")
        logger.info(
            "http_request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "route": getattr(route, "path", "unmatched"),
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(faces.router)
    app.include_router(verifications.router)
    app.include_router(liveness.router)
    if settings.app_env == "local":
        # Página de demonstração do liveness ativo; nunca montada fora de local.
        app.include_router(dev.router)
    configure_tracing(app, enabled=settings.otel_enabled, service_name=settings.otel_service_name)
    return app
