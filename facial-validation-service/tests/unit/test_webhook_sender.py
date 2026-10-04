"""Sender real (httpx) contra um transporte simulado: nada sai para a rede."""

import hashlib
import hmac
import time
import uuid

import httpx
import pytest

from app.application.ports import WebhookTransportError
from app.infrastructure.webhooks import HttpxWebhookSender

BODY = b'{"id":"x","type":"verification.completed","data":{}}'


def sender(handler) -> HttpxWebhookSender:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return HttpxWebhookSender(timeout_seconds=5, client=client)


async def test_posts_signed_body_with_event_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(204)

    event_id = uuid.uuid4()
    status = await sender(handler).deliver(
        url="https://consumidor.example/hook",
        secret="whsec_abc",
        event_id=event_id,
        event_type="verification.completed",
        body=BODY,
    )
    request = seen["request"]
    assert status == 204 and request.method == "POST" and request.content == BODY
    assert request.headers["X-FVS-Event-Id"] == str(event_id)
    assert request.headers["X-FVS-Event-Type"] == "verification.completed"

    # O consumidor valida assim (docs/api.md):
    parts = dict(p.split("=", 1) for p in request.headers["X-FVS-Signature"].split(","))
    expected = hmac.new(b"whsec_abc", f"{parts['t']}.".encode() + BODY, hashlib.sha256)
    assert hmac.compare_digest(parts["v1"], expected.hexdigest())
    assert abs(int(parts["t"]) - time.time()) < 5


async def test_does_not_follow_redirects():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest"})

    status = await sender(handler).deliver(
        url="https://consumidor.example/hook",
        secret="s",
        event_id=uuid.uuid4(),
        event_type="t",
        body=BODY,
    )
    assert status == 302 and calls == ["https://consumidor.example/hook"]


@pytest.mark.parametrize(
    ("exception", "code"),
    [
        (httpx.ReadTimeout("lento"), "TIMEOUT"),
        (httpx.ConnectError("recusado"), "CONNECTION_ERROR"),
    ],
)
async def test_transport_errors_become_short_codes(exception, code):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception

    with pytest.raises(WebhookTransportError) as raised:
        await sender(handler).deliver(
            url="https://consumidor.example/hook",
            secret="s",
            event_id=uuid.uuid4(),
            event_type="t",
            body=BODY,
        )
    assert raised.value.code == code
