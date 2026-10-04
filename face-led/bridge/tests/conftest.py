from face_led_bridge.protocol import FIRMWARE_VERSION, Command, expected_reply


class FakeFirmware:
    """Porta serial que imita o firmware: READY ao abrir, resposta por comando."""

    def __init__(self, *, boots: bool = True, broken: bool = False) -> None:
        self.lines: list[bytes] = [f"READY {FIRMWARE_VERSION}\n".encode()] if boots else []
        self.received: list[str] = []
        self.closed = False
        self.broken = broken

    def write(self, data: bytes) -> int:
        if self.broken:
            raise OSError("porta caiu")
        command = data.decode().strip()
        self.received.append(command)
        try:
            reply = expected_reply(Command(command))
        except ValueError:
            reply = "ERR UNKNOWN"
        self.lines.append(f"{reply}\n".encode())
        return len(data)

    def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""

    def reset_input_buffer(self) -> None:
        self.lines.clear()

    def close(self) -> None:
        self.closed = True


class Opener:
    """Fábrica de portas para Board: entrega as portas da fila, na ordem."""

    def __init__(self, *ports: FakeFirmware) -> None:
        self.ports = list(ports)
        self.opened: list[FakeFirmware] = []

    def __call__(self, port: str, baud: int, timeout: float) -> FakeFirmware:
        fake = self.ports.pop(0)
        self.opened.append(fake)
        return fake
