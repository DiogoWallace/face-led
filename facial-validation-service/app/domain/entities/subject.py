from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(kw_only=True)
class Subject:
    """Pessoa identificada pelo `external_id` do sistema consumidor, dentro do tenant."""

    id: UUID
    tenant_id: UUID
    external_id: str
    created_at: datetime
    updated_at: datetime
