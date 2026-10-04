"""Assinatura do webhook (ADR-009 do facial-validation-service).

X-FVS-Signature: t=<unix>,v1=<hex(HMAC_SHA256(segredo, f"{t}.{corpo}"))>
O segredo é a string `whsec_...` inteira. Assinaturas mais velhas que a
tolerância são recusadas (um corpo capturado não pode ser reenviado depois).
"""

import hashlib
import hmac


class InvalidSignature(Exception):
    pass


def sign(secret: str, timestamp: int, body: bytes) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={digest.hexdigest()}"


def verify(secret: str, header: str | None, body: bytes, *, now: int, tolerance: int) -> None:
    if not header:
        raise InvalidSignature("sem assinatura")
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    try:
        timestamp = int(parts["t"])
        received = parts["v1"]
    except (KeyError, ValueError):
        raise InvalidSignature("assinatura malformada") from None
    if abs(now - timestamp) > tolerance:
        raise InvalidSignature("assinatura fora da janela de tempo")
    expected = sign(secret, timestamp, body).split("v1=", 1)[1]
    if not hmac.compare_digest(expected, received):
        raise InvalidSignature("assinatura não confere")
