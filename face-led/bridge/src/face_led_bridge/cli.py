"""face-led: send (teste manual), serve (receptor do webhook) e ports."""

import argparse
import logging
import sys

from face_led_bridge.board import Board, BoardError
from face_led_bridge.config import Settings
from face_led_bridge.protocol import Command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="face-led")
    sub = parser.add_subparsers(dest="action", required=True)
    send = sub.add_parser("send", help="envia um comando à placa e mostra a resposta")
    send.add_argument("command", choices=[c.value for c in Command])
    send.add_argument("--port", help="porta serial (padrão: FACE_LED_SERIAL_PORT)")
    sub.add_parser("serve", help="recebe o webhook do facial-validation-service")
    sub.add_parser("ports", help="lista as portas seriais visíveis")
    args = parser.parse_args(argv)
    settings = Settings()

    if args.action == "ports":
        from serial.tools import list_ports

        for p in list_ports.comports():
            print(f"{p.device}\t{p.description}\t{p.hwid}")
        return 0

    if args.action == "send":
        board = Board(args.port or settings.serial_port, settings.baud)
        try:
            print(board.send(Command(args.command)))
        except BoardError as error:
            print(f"erro: {error}", file=sys.stderr)
            return 1
        finally:
            board.close()
        return 0

    import uvicorn

    from face_led_bridge.app import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    board = Board(settings.serial_port, settings.baud)
    try:
        uvicorn.run(create_app(settings, board), host=settings.host, port=settings.port)
    finally:
        board.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
