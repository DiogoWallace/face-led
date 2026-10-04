from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


@dataclass(kw_only=True)
class Tenant:
    """Sistema consumidor (projeto da empresa) isolado dos demais."""

    id: UUID
    name: str
    slug: str
    status: TenantStatus
    api_key_hash: str = field(repr=False)
    created_at: datetime
    updated_at: datetime
    # Webhook de resultado (ADR-009). Configurado só pelo operador (CLI): quem chama a
    # API não escolhe para onde o serviço faz requisições. O segredo fica CIFRADO.
    webhook_url: str | None = None
    webhook_secret: bytes | None = field(default=None, repr=False)

    @property
    def is_active(self) -> bool:
        return self.status is TenantStatus.ACTIVE

    @property
    def webhook_enabled(self) -> bool:
        return self.webhook_url is not None and self.webhook_secret is not None

    def configure_webhook(self, *, url: str, encrypted_secret: bytes, now: datetime) -> None:
        self.webhook_url, self.webhook_secret = url, encrypted_secret
        self.updated_at = now

    def disable_webhook(self, now: datetime) -> None:
        self.webhook_url, self.webhook_secret = None, None
        self.updated_at = now
