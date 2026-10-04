"""Mapeamento de exceções para respostas HTTP padronizadas, sem ecoar a entrada."""

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.application.ports import CaptureStorageError, TaskQueueError
from app.domain import exceptions as exc

logger = logging.getLogger(__name__)

_STATUS: dict[type[exc.DomainError], int] = {
    exc.AuthenticationFailed: status.HTTP_401_UNAUTHORIZED,
    exc.InvalidCapture: status.HTTP_422_UNPROCESSABLE_CONTENT,
    exc.SubjectNotFound: status.HTTP_404_NOT_FOUND,
    exc.VerificationNotFound: status.HTTP_404_NOT_FOUND,
    exc.FaceRegistrationNotFound: status.HTTP_404_NOT_FOUND,
    exc.LivenessSessionNotFound: status.HTTP_404_NOT_FOUND,
    exc.LivenessSessionInvalid: status.HTTP_409_CONFLICT,
    exc.LivenessEvidenceRequired: status.HTTP_422_UNPROCESSABLE_CONTENT,
    exc.SubjectNotEnrolled: status.HTTP_409_CONFLICT,
    exc.SubjectAlreadyEnrolled: status.HTTP_409_CONFLICT,
    exc.IdempotencyConflict: status.HTTP_409_CONFLICT,
    exc.DuplicateIdempotencyKey: status.HTTP_409_CONFLICT,
}


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    headers = {"WWW-Authenticate": "ApiKey"} if status_code == 401 else None
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(exc.DomainError)
    async def domain_error(_: Request, error: exc.DomainError) -> JSONResponse:
        status_code = _STATUS.get(type(error), status.HTTP_400_BAD_REQUEST)
        return error_response(status_code, error.code, str(error))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        # Apenas localização e tipo do erro; o valor enviado nunca é devolvido.
        fields = sorted({".".join(str(p) for p in e.get("loc", ())) for e in error.errors()})
        return error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "VALIDATION_ERROR",
            f"requisição inválida: {', '.join(fields)}",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, error: StarletteHTTPException) -> JSONResponse:
        return error_response(error.status_code, "HTTP_ERROR", str(error.detail))

    @app.exception_handler(TaskQueueError)
    @app.exception_handler(CaptureStorageError)
    async def dependency_error(_: Request, error: Exception) -> JSONResponse:
        logger.error("dependency_unavailable", extra={"error_type": type(error).__name__})
        return error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE, "DEPENDENCY_UNAVAILABLE", "serviço indisponível"
        )

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, error: Exception) -> JSONResponse:
        logger.exception("unhandled_error", extra={"error_type": type(error).__name__})
        return error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "erro interno"
        )
