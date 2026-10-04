"""Protocolo serial com o firmware (docs/protocol.md) e o mapa resultado → comando."""

from enum import StrEnum
from typing import Any

FIRMWARE_VERSION = "face-led/1"


class Command(StrEnum):
    PING = "PING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"
    OFF = "OFF"


def expected_reply(command: Command) -> str:
    if command is Command.PING:
        return f"PONG {FIRMWARE_VERSION}"
    return f"ACK {command.value}"


# Decisão do serviço (APPROVED | REJECTED | ERROR; EXPIRED já chega como ERROR).
_DECISIONS = {
    "APPROVED": Command.APPROVED,
    "REJECTED": Command.REJECTED,
    "ERROR": Command.ERROR,
}


def command_for_event(event: dict[str, Any], event_types: frozenset[str]) -> Command | None:
    """Comando para um evento do webhook, ou None quando o evento não interessa."""
    if event.get("type") not in event_types:
        return None
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    return _DECISIONS.get(data.get("decision"))
