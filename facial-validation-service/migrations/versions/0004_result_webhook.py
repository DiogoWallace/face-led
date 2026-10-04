"""webhook de resultado: configuração por tenant e outbox de entregas (ADR-009)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("tenants", sa.Column("webhook_url", sa.String(2048)))
    # Segredo de assinatura CIFRADO (AES-GCM com AAD do tenant), nunca em claro.
    op.add_column("tenants", sa.Column("webhook_secret", sa.LargeBinary()))
    op.create_check_constraint(
        "ck_tenants_webhook_complete",
        "tenants",
        "(webhook_url IS NULL) = (webhook_secret IS NULL)",
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", TZ, nullable=False),
        sa.Column("last_attempt_at", TZ),
        sa.Column("last_status_code", sa.Integer()),
        sa.Column("last_error", sa.String(64)),
        sa.Column("delivered_at", TZ),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("updated_at", TZ, nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','DELIVERED','FAILED')", name="ck_webhook_deliveries_status"
        ),
        sa.CheckConstraint(
            "event_type IN ('face_registration.completed','verification.completed')",
            name="ck_webhook_deliveries_event_type",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_webhook_deliveries_attempts"),
    )
    op.create_index(
        "ix_webhook_deliveries_due",
        "webhook_deliveries",
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index("ix_webhook_deliveries_resource", "webhook_deliveries", ["resource_id"])


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_resource", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_due", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_constraint("ck_tenants_webhook_complete", "tenants", type_="check")
    op.drop_column("tenants", "webhook_secret")
    op.drop_column("tenants", "webhook_url")
