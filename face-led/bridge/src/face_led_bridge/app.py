"""Receptor do webhook: confere assinatura, deduplica e aciona a placa.

Respostas pensadas para a política de tentativas do serviço (só 2xx entrega):
- 401 assinatura inválida (o serviço tenta de novo; segredo errado aparece no log);
- 200 evento aceito, ignorado ou repetido;
- 503 placa indisponível: o serviço tenta de novo mais tarde.
"""

import json
import logging
import time
from collections import OrderedDict
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from face_led_bridge.board import Board, BoardError
from face_led_bridge.config import Settings
from face_led_bridge.protocol import command_for_event
from face_led_bridge.signature import InvalidSignature, verify

logger = logging.getLogger("face_led_bridge")


class SeenEvents:
    """Ids já processados (entrega "pelo menos uma vez"): memória limitada, sem disco."""

    def __init__(self, capacity: int = 1000) -> None:
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._capacity = capacity

    def __contains__(self, event_id: str) -> bool:
        return event_id in self._ids

    def add(self, event_id: str) -> None:
        self._ids[event_id] = None
        while len(self._ids) > self._capacity:
            self._ids.popitem(last=False)


def create_app(settings: Settings, board: Board, clock=time.time) -> FastAPI:
    if settings.webhook_secret is None:
        raise ValueError("FACE_LED_WEBHOOK_SECRET é obrigatório para receber o webhook")
    secret = settings.webhook_secret.get_secret_value()
    seen = SeenEvents()
    app = FastAPI(title="face-led bridge", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook(request: Request) -> JSONResponse:
        body = await request.body()
        try:
            verify(
                secret,
                request.headers.get("X-FVS-Signature"),
                body,
                now=int(clock()),
                tolerance=settings.signature_tolerance_seconds,
            )
        except InvalidSignature as error:
            logger.warning("webhook recusado: %s", error)
            return JSONResponse({"error": "invalid_signature"}, status_code=401)

        event_id = request.headers.get("X-FVS-Event-Id", "")
        if event_id and event_id in seen:
            return JSONResponse({"status": "duplicate"})
        try:
            event: dict[str, Any] = json.loads(body)
        except ValueError:
            return JSONResponse({"error": "invalid_json"}, status_code=400)

        command = command_for_event(event, settings.event_type_set)
        if command is None:
            if event_id:
                seen.add(event_id)
            return JSONResponse({"status": "ignored"})
        try:
            reply = await run_in_threadpool(board.send, command)
        except BoardError as error:
            logger.warning("placa indisponível: %s", error)
            return JSONResponse({"error": "board_unavailable"}, status_code=503)
        if event_id:
            seen.add(event_id)
        # Só o tipo e o comando: nada de subject_id (pode ser dado pessoal) no log.
        logger.info("evento %s -> %s (%s)", event.get("type"), command.value, reply)
        return JSONResponse({"status": "sent", "command": command.value})

    return app
