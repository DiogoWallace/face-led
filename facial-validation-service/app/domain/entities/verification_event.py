from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(kw_only=True)
class VerificationEvent:
    """Trilha de auditoria. `data` nunca contém imagem, embedding, token ou segredo."""

    id: UUID
    tenant_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    data: dict[str, Any]
    occurred_at: datetime
