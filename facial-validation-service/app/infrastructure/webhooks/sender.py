"""Envio do webhook (ADR-009): HMAC-SHA256 com timestamp, sem seguir redirect.

Cabeçalhos:
    X-FVS-Event-Id    id do evento (deduplicação no consumidor: entrega "pelo menos uma vez")
    X-FVS-Event-Type  face_registration.completed | verification.completed
    X-FVS-Signature   t=<unix>,v1=<hex(HMAC_SHA256(segredo, f"{t}.{corpo}"))>

O timestamp é assinado a cada tentativa: o consumidor recusa assinaturas velhas
(sugestão: 5 min) e um corpo capturado não pode ser reenviado depois.

Nada da resposta do consumidor é guardado ou registrado em log, só o status.
"""

import hashlib
import hmac
import time
from uuid import UUID

import httpx

from app.application.ports import WebhookTransportError

USER_AGENT = "facial-validation-service-webhook/1"


def sign(secret: str, timestamp: int, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


class HttpxWebhookSender:
    def __init__(self, *, timeout_seconds: float, client: httpx.AsyncClient | None = None) -> None:
        # Sem redirect: o destino é só o que o operador configurou.
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds, follow_redirects=False, trust_env=False
        )

    async def deliver(
        self, *, url: str, secret: str, event_id: UUID, event_type: str, body: bytes
    ) -> int:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-FVS-Event-Id": str(event_id),
            "X-FVS-Event-Type": event_type,
            "X-FVS-Signature": sign(secret, int(time.time()), body),
        }
        try:
            response = await self._client.post(url, content=body, headers=headers)
        except httpx.TimeoutException:
            raise WebhookTransportError("TIMEOUT") from None
        except httpx.HTTPError:
            raise WebhookTransportError("CONNECTION_ERROR") from None
        return response.status_code

    async def close(self) -> None:
        await self._client.aclose()
