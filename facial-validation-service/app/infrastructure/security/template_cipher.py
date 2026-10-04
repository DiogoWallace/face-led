"""Criptografia do template biométrico em repouso (AES-256-GCM).

Formato: 1 byte de versão da chave + 12 bytes de nonce + ciphertext/tag.
A troca por KMS/Vault com rotação é decisão pendente (ADR-004).
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEY_VERSION = 1
_NONCE_SIZE = 12


class AesGcmTemplateCipher:
    def __init__(self, key_b64: str) -> None:
        key = base64.urlsafe_b64decode(key_b64.encode())
        if len(key) != 32:
            raise ValueError("TEMPLATE_ENCRYPTION_KEY deve ter 32 bytes em base64")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        nonce = os.urandom(_NONCE_SIZE)
        return bytes([_KEY_VERSION]) + nonce + self._aead.encrypt(nonce, plaintext, associated_data)

    def decrypt(self, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        if not ciphertext or ciphertext[0] != _KEY_VERSION:
            raise ValueError("versão de chave desconhecida")
        nonce = ciphertext[1 : 1 + _NONCE_SIZE]
        return self._aead.decrypt(nonce, ciphertext[1 + _NONCE_SIZE :], associated_data)


def generate_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()
