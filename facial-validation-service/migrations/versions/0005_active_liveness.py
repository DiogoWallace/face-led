"""liveness ativo próprio: sessões de desafio e vínculo com cadastro/validação (ADR-010)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "liveness_challenges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("external_subject_id", sa.String(128), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("steps", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", TZ, nullable=False),
        sa.Column("used_at", TZ),
        sa.Column("used_by_id", sa.Uuid()),
        sa.Column("frame_keys", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("updated_at", TZ, nullable=False),
        sa.CheckConstraint(
            "purpose IN ('REGISTRATION','VERIFICATION')", name="ck_liveness_challenges_purpose"
        ),
        sa.CheckConstraint("status IN ('CREATED','USED')", name="ck_liveness_challenges_status"),
        sa.CheckConstraint(
            "(status = 'USED') = (used_at IS NOT NULL AND used_by_id IS NOT NULL)",
            name="ck_liveness_challenges_used_consistent",
        ),
    )
    op.create_index("ix_liveness_challenges_tenant", "liveness_challenges", ["tenant_id"])

    for table in ("face_registrations", "verifications"):
        op.add_column(
            table,
            sa.Column(
                "liveness_challenge_id",
                sa.Uuid(),
                sa.ForeignKey("liveness_challenges.id", ondelete="RESTRICT"),
            ),
        )
        # Uma sessão de desafio prova UMA operação (uso único também no banco).
        op.create_index(
            f"uq_{table}_liveness_challenge",
            table,
            ["liveness_challenge_id"],
            unique=True,
            postgresql_where=sa.text("liveness_challenge_id IS NOT NULL"),
        )

    op.add_column("liveness_sessions", sa.Column("detail", sa.String(64)))


def downgrade() -> None:
    op.drop_column("liveness_sessions", "detail")
    for table in ("verifications", "face_registrations"):
        op.drop_index(f"uq_{table}_liveness_challenge", table_name=table)
        op.drop_column(table, "liveness_challenge_id")
    op.drop_index("ix_liveness_challenges_tenant", table_name="liveness_challenges")
    op.drop_table("liveness_challenges")
