"""quality issues em cadastros e validações

Códigos do QualityGate expostos na API (Fase 6). NULL = qualidade não avaliada.
Linhas existentes ficam NULL: os códigos antigos só existem nos eventos.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("face_registrations", "verifications"):
        op.add_column(table, sa.Column("quality_issues", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    for table in ("verifications", "face_registrations"):
        op.drop_column(table, "quality_issues")
