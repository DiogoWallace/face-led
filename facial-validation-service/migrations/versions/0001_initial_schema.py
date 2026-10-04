"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("api_key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("updated_at", TZ, nullable=False),
        sa.CheckConstraint("status IN ('ACTIVE','SUSPENDED')", name="ck_tenants_status"),
    )

    op.create_table(
        "subjects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("updated_at", TZ, nullable=False),
        sa.UniqueConstraint("tenant_id", "external_id", name="uq_subjects_tenant_ext"),
    )

    process_status = "status IN ('CREATED','PROCESSING','APPROVED','REJECTED','ERROR','EXPIRED')"

    op.create_table(
        "face_registrations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "subject_id",
            sa.Uuid(),
            sa.ForeignKey("subjects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(64)),
        sa.Column("capture_object_key", sa.String(512)),
        sa.Column("model_name", sa.String(100)),
        sa.Column("model_version", sa.String(100)),
        sa.Column("template", sa.LargeBinary()),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("updated_at", TZ, nullable=False),
        sa.CheckConstraint(process_status, name="ck_face_registrations_status"),
        sa.CheckConstraint(
            "status <> 'APPROVED' OR (template IS NOT NULL AND model_name IS NOT NULL AND model_version IS NOT NULL)",
            name="ck_face_registrations_approved_has_template",
        ),
    )
    op.create_index(
        "ix_face_registrations_tenant_subject", "face_registrations", ["tenant_id", "subject_id"]
    )
    op.create_index(
        "uq_face_registrations_active_subject",
        "face_registrations",
        ["subject_id"],
        unique=True,
        postgresql_where=sa.text("status = 'APPROVED'"),
    )

    op.create_table(
        "liveness_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "subject_id",
            sa.Uuid(),
            sa.ForeignKey("subjects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("reference_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("verdict", sa.String(20)),
        sa.Column("score", sa.Float()),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("completed_at", TZ),
        sa.CheckConstraint(
            "purpose IN ('REGISTRATION','VERIFICATION')", name="ck_liveness_purpose"
        ),
    )
    op.create_index("ix_liveness_sessions_reference", "liveness_sessions", ["reference_id"])

    op.create_table(
        "verifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "subject_id",
            sa.Uuid(),
            sa.ForeignKey("subjects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "face_registration_id",
            sa.Uuid(),
            sa.ForeignKey("face_registrations.id", ondelete="RESTRICT"),
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(64)),
        sa.Column("similarity", sa.Float()),
        sa.Column("policy_version", sa.String(50)),
        sa.Column("capture_object_key", sa.String(512)),
        sa.Column("expires_at", TZ, nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("updated_at", TZ, nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_verifications_idempotency"),
        sa.CheckConstraint(process_status, name="ck_verifications_status"),
    )
    op.create_index("ix_verifications_tenant_subject", "verifications", ["tenant_id", "subject_id"])

    op.create_table(
        "verification_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("aggregate_type", sa.String(50), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column(
            "data", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("occurred_at", TZ, nullable=False),
    )
    op.create_index(
        "ix_verification_events_aggregate", "verification_events", ["aggregate_id", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_table("verification_events")
    op.drop_table("verifications")
    op.drop_table("liveness_sessions")
    op.drop_table("face_registrations")
    op.drop_table("subjects")
    op.drop_table("tenants")
