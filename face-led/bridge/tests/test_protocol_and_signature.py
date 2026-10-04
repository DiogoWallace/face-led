import hashlib
import hmac

import pytest

from face_led_bridge.protocol import Command, command_for_event
from face_led_bridge.signature import InvalidSignature, sign, verify

VERIFICATIONS = frozenset({"verification.completed"})


@pytest.mark.parametrize(
    ("decision", "command"),
    [("APPROVED", Command.APPROVED), ("REJECTED", Command.REJECTED), ("ERROR", Command.ERROR)],
)
def test_decision_maps_to_command(decision, command):
    event = {"type": "verification.completed", "data": {"decision": decision}}
    assert command_for_event(event, VERIFICATIONS) is command


@pytest.mark.parametrize(
    "event",
    [
        {"type": "face_registration.completed", "data": {"decision": "APPROVED"}},
        {"type": "verification.completed", "data": {"decision": None}},
        {"type": "verification.completed", "data": "x"},
        {"type": "verification.completed"},
    ],
)
def test_other_events_are_ignored(event):
    assert command_for_event(event, VERIFICATIONS) is None


SECRET = "whsec_test"
BODY = b'{"id":"1"}'


def test_signature_matches_service_format():
    # Mesmo cálculo do sender do serviço: HMAC_SHA256(segredo, f"{t}.{corpo}").
    digest = hmac.new(SECRET.encode(), b"1700000000." + BODY, hashlib.sha256).hexdigest()
    assert sign(SECRET, 1700000000, BODY) == f"t=1700000000,v1={digest}"


def test_valid_signature_passes():
    verify(SECRET, sign(SECRET, 1000, BODY), BODY, now=1100, tolerance=300)


@pytest.mark.parametrize(
    ("header", "now"),
    [
        (None, 1000),
        ("lixo", 1000),
        ("t=abc,v1=00", 1000),
        (sign("whsec_outro", 1000, BODY), 1000),
        (sign(SECRET, 1000, BODY), 1301),  # velha demais
        (sign(SECRET, 1000, b'{"id":"2"}'), 1000),  # corpo trocado
    ],
)
def test_invalid_signatures_are_refused(header, now):
    with pytest.raises(InvalidSignature):
        verify(SECRET, header, BODY, now=now, tolerance=300)
