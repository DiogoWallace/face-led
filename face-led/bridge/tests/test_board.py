import pytest

from face_led_bridge.board import Board, BoardError
from face_led_bridge.protocol import Command
from tests.conftest import FakeFirmware, Opener


def board(opener: Opener) -> Board:
    return Board("fake", boot_timeout=0.2, reply_timeout=0.05, opener=opener)


def test_waits_ready_then_sends():
    fake = FakeFirmware()
    assert board(Opener(fake)).send(Command.APPROVED) == "ACK APPROVED"
    assert fake.received == ["APPROVED"]


def test_keeps_port_open_between_commands():
    opener = Opener(FakeFirmware())
    b = board(opener)
    b.send(Command.APPROVED)
    b.send(Command.REJECTED)
    assert len(opener.opened) == 1


def test_board_without_reset_is_confirmed_with_ping():
    fake = FakeFirmware(boots=False)
    assert board(Opener(fake)).send(Command.ERROR) == "ACK ERROR"
    assert fake.received == ["PING", "ERROR"]


def test_reconnects_once_when_port_dropped():
    first, second = FakeFirmware(), FakeFirmware()
    opener = Opener(first, second)
    b = board(opener)
    b.send(Command.APPROVED)
    first.broken = True
    assert b.send(Command.APPROVED) == "ACK APPROVED"
    assert first.closed and len(opener.opened) == 2


def test_silent_port_is_board_error():
    class Silent(FakeFirmware):
        def write(self, data: bytes) -> int:
            return len(data)

    with pytest.raises(BoardError):
        board(Opener(Silent(boots=False), Silent(boots=False))).send(Command.APPROVED)
