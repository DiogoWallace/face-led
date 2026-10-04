"""Portas (hexagonal) que a aplicação exige da infraestrutura."""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.application.dto import FaceRegistrationView, VerificationView
from app.application.ports.biometric import (
    BiometricProviderError,
    BiometricProviderNotConfigured,
    FaceAnalysisComponents,
    FaceComparator,
    FaceDetector,
    FaceEmbedder,
    FaceQualityAssessor,
    LivenessNotConfigured,
    LivenessProvider,
)
from app.domain.repositories import UnitOfWork
from app.domain.value_objects import CaptureData

__all__ = [
    "BiometricProviderError",
    "BiometricProviderNotConfigured",
    "CaptureStorage",
    "CaptureStorageError",
    "Clock",
    "FaceAnalysisComponents",
    "FaceComparator",
    "FaceDetector",
    "FaceEmbedder",
    "FaceQualityAssessor",
    "IdGenerator",
    "LivenessNotConfigured",
    "LivenessProvider",
    "TaskQueue",
    "TaskQueueError",
    "TemplateCipher",
    "UnitOfWorkFactory",
    "ResultRenderer",
    "WebhookSender",
    "WebhookTransportError",
]


class CaptureStorageError(Exception):
    pass


class CaptureStorage(Protocol):
    """Object storage privado (S3 compatível) para capturas."""

    async def put(self, key: str, capture: CaptureData) -> None: ...
    async def get(self, key: str) -> CaptureData: ...
    async def delete(self, key: str) -> None: ...
    async def temporary_url(self, key: str, expires_in_seconds: int) -> str: ...
    async def check(self) -> None: ...


class TaskQueueError(Exception):
    pass


class TaskQueue(Protocol):
    async def enqueue_face_registration(self, registration_id: UUID) -> None: ...
    async def enqueue_verification(self, verification_id: UUID) -> None: ...
    async def enqueue_webhook_delivery(self, delivery_id: UUID) -> None: ...


class WebhookTransportError(Exception):
    """O envio não chegou a receber resposta HTTP. `code` é curto e seguro para log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class WebhookSender(Protocol):
    async def deliver(
        self, *, url: str, secret: str, event_id: UUID, event_type: str, body: bytes
    ) -> int:
        """Assina e envia; devolve o status HTTP. Falha de transporte: WebhookTransportError."""
        ...


class ResultRenderer(Protocol):
    """Serializa o resultado exatamente como o GET da API (ADR-007): sem score nem medidas."""

    def face_registration(self, view: FaceRegistrationView) -> dict[str, Any]: ...
    def verification(self, view: VerificationView) -> dict[str, Any]: ...


class TemplateCipher(Protocol):
    """Criptografia do template biométrico em repouso."""

    def encrypt(self, plaintext: bytes, *, associated_data: bytes) -> bytes: ...
    def decrypt(self, ciphertext: bytes, *, associated_data: bytes) -> bytes: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
IdGenerator = Callable[[], UUID]
