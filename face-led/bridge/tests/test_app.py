import json

import pytest
from fastapi.testclient import TestClient

from face_led_bridge.app import create_app
from face_led_bridge.board import BoardError
from face_led_bridge.config import Settings
from face_led_bridge.protocol import Command, expected_reply
from face_led_bridge.signature import sign

SECRET = "whsec_test"
NOW = 1_700_000_000


class FakeBoard:
    def __init__(self) -> None:
        self.sent: list[Command] = []
        self.down = False

    def send(self, command: Command) -> str:
        if self.down:
            raise BoardError("desconectada")
        self.sent.append(command)
        return expected_reply(command)


@pytest.fixture
def setup():
    board = FakeBoard()
    settings = Settings(webhook_secret=SECRET, _env_file=None)
    client = TestClient(create_app(settings, board, clock=lambda: NOW))
    return client, board


def post(client, event: dict, event_id: str = "evt-1", secret: str = SECRET):
    body = json.dumps(event).encode()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "X-FVS-Signature": sign(secret, NOW, body),
            "X-FVS-Event-Id": event_id,
            "Content-Type": "application/json",
        },
    )


APPROVED = {"type": "verification.completed", "data": {"decision": "APPROVED", "subject_id": "x"}}


def test_approved_verification_lights_led(setup):
    client, board = setup
    r = post(client, APPROVED)
    assert r.status_code == 200 and r.json() == {"status": "sent", "command": "APPROVED"}
    assert board.sent == [Command.APPROVED]


def test_bad_signature_is_401_and_board_untouched(setup):
    client, board = setup
    assert post(client, APPROVED, secret="whsec_outro").status_code == 401
    assert board.sent == []


def test_duplicate_event_is_not_resent(setup):
    client, board = setup
    post(client, APPROVED)
    r = post(client, APPROVED)
    assert r.json() == {"status": "duplicate"} and board.sent == [Command.APPROVED]


def test_registration_event_is_ignored(setup):
    client, board = setup
    event = {"type": "face_registration.completed", "data": {"decision": "APPROVED"}}
    assert post(client, event).json() == {"status": "ignored"}
    assert board.sent == []


def test_board_down_is_503_and_retry_works(setup):
    client, board = setup
    board.down = True
    assert post(client, APPROVED).status_code == 503
    board.down = False  # o serviço tenta de novo com o mesmo id de evento
    assert post(client, APPROVED).json()["status"] == "sent"


def test_secret_is_required():
    with pytest.raises(ValueError):
        create_app(Settings(_env_file=None), FakeBoard())
