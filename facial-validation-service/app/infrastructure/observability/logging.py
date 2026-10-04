"""Logging estruturado (JSON) com redação de campos sensíveis.

Nunca registrar: selfie, vídeo, imagem de documento, embedding, tokens, API keys, secrets.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

SENSITIVE_KEYS = frozenset(
    {
        "image",
        "selfie",
        "video",
        "document",
        "capture",
        "content",
        "embedding",
        "template",
        "vector",
        "token",
        "api_key",
        "authorization",
        "x-api-key",
        "secret",
        "password",
        "access_key",
        "secret_key",
    }
)
REDACTED = "[REDACTED]"

_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys() | {"message", "asctime"}
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: REDACTED if str(k).lower() in SENSITIVE_KEYS else redact(v) for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<{len(value)} bytes>"
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if request_id := request_id_var.get():
            payload["request_id"] = request_id
        extras = {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}
        payload.update(redact(extras))
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Access log do uvicorn é substituído pelo middleware da aplicação.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("botocore", "boto3", "urllib3", "s3transfer"):
        logging.getLogger(name).setLevel(logging.WARNING)
