"""Conexão serial com a placa: porta aberta o tempo todo, um comando por vez.

Abrir a porta reinicia o Uno (DTR). Por isso a conexão fica aberta e, ao abrir,
espera o "READY" do firmware (ou responde a um PING) antes de aceitar comandos.
"""

import threading
import time
from collections.abc import Callable
from typing import Protocol

import serial

from face_led_bridge.protocol import FIRMWARE_VERSION, Command, expected_reply


class BoardError(Exception):
    pass


class SerialPort(Protocol):
    def write(self, data: bytes) -> int | None: ...
    def readline(self) -> bytes: ...
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


def open_serial(port: str, baud: int, timeout: float) -> SerialPort:
    return serial.serial_for_url(port, baudrate=baud, timeout=timeout)


class Board:
    def __init__(
        self,
        port: str,
        baud: int = 115200,
        *,
        boot_timeout: float = 4.0,
        reply_timeout: float = 1.0,
        opener: Callable[[str, int, float], SerialPort] = open_serial,
    ) -> None:
        self._port = port
        self._baud = baud
        self._boot_timeout = boot_timeout
        self._reply_timeout = reply_timeout
        self._opener = opener
        self._serial: SerialPort | None = None
        self._lock = threading.Lock()

    def send(self, command: Command) -> str:
        """Envia o comando e devolve a resposta. Reabre a porta uma vez se ela caiu."""
        with self._lock:
            try:
                return self._send(command)
            except (BoardError, serial.SerialException, OSError):
                self._close()
            try:
                return self._send(command)
            except (serial.SerialException, OSError) as error:
                self._close()
                raise BoardError(f"placa indisponível em {self._port}: {error}") from error

    def close(self) -> None:
        with self._lock:
            self._close()

    def _send(self, command: Command) -> str:
        port = self._connect()
        port.write(f"{command.value}\n".encode())
        reply = self._read_line(port, self._reply_timeout)
        if reply != expected_reply(command):
            raise BoardError(f"resposta inesperada da placa: {reply!r}")
        return reply

    def _connect(self) -> SerialPort:
        if self._serial is not None:
            return self._serial
        port = self._opener(self._port, self._baud, self._reply_timeout)
        deadline = time.monotonic() + self._boot_timeout
        ready = f"READY {FIRMWARE_VERSION}"
        while time.monotonic() < deadline:
            if self._read_line(port, self._reply_timeout) == ready:
                break
        else:
            # Placa que não reiniciou ao abrir (ex.: já estava rodando): confere com PING.
            port.reset_input_buffer()
            port.write(b"PING\n")
            if self._read_line(port, self._reply_timeout) != expected_reply(Command.PING):
                port.close()
                raise BoardError(f"firmware {FIRMWARE_VERSION} não respondeu em {self._port}")
        self._serial = port
        return port

    @staticmethod
    def _read_line(port: SerialPort, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = port.readline()
            if raw:
                return raw.decode(errors="replace").strip()
        return ""

    def _close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None
