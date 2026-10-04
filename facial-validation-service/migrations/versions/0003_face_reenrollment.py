"""recadastro: substituição da referência biométrica (ADR-008)

- replaces_registration_id: o envio é um recadastro (PUT) da referência indicada;
- superseded_at / superseded_by_id: esta referência foi substituída;
- índice de referência ativa passa a ignorar as substituídas;
- referência substituída NÃO pode manter template (apagado ao substituir).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "face_registrations"
ACTIVE_INDEX = "uq_face_registrations_active_subject"
APPROVED_CHECK = "ck_face_registrations_approved_has_template"
_HAS_MODEL = "model_name IS NOT NULL AND model_version IS NOT NULL"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            "replaces_registration_id",
            sa.Uuid(),
            sa.ForeignKey(f"{TABLE}.id", ondelete="RESTRICT"),
        ),
    )
    op.add_column(TABLE, sa.Column("superseded_at", sa.DateTime(timezone=True)))
    op.add_column(
        TABLE,
        sa.Column("superseded_by_id", sa.Uuid(), sa.ForeignKey(f"{TABLE}.id", ondelete="RESTRICT")),
    )

    op.drop_index(ACTIVE_INDEX, table_name=TABLE)
    op.create_index(
        ACTIVE_INDEX,
        TABLE,
        ["subject_id"],
        unique=True,
        postgresql_where=sa.text("status = 'APPROVED' AND superseded_at IS NULL"),
    )

    # APPROVED ativa exige template; APPROVED substituída exige template APAGADO.
    op.drop_constraint(APPROVED_CHECK, TABLE, type_="check")
    op.create_check_constraint(
        APPROVED_CHECK,
        TABLE,
        f"status <> 'APPROVED' OR ({_HAS_MODEL} AND "
        "((superseded_at IS NULL AND template IS NOT NULL) "
        "OR (superseded_at IS NOT NULL AND template IS NULL)))",
    )
    op.create_check_constraint(
        "ck_face_registrations_superseded_consistent",
        TABLE,
        "(superseded_at IS NULL) = (superseded_by_id IS NULL) "
        "AND (superseded_at IS NULL OR status = 'APPROVED')",
    )


def downgrade() -> None:
    # Referências substituídas não têm template e quebrariam a regra antiga:
    # o downgrade só é possível se nenhum recadastro foi concluído.
    op.drop_constraint("ck_face_registrations_superseded_consistent", TABLE, type_="check")
    op.drop_constraint(APPROVED_CHECK, TABLE, type_="check")
    op.create_check_constraint(
        APPROVED_CHECK,
        TABLE,
        f"status <> 'APPROVED' OR (template IS NOT NULL AND {_HAS_MODEL})",
    )
    op.drop_index(ACTIVE_INDEX, table_name=TABLE)
    op.create_index(
        ACTIVE_INDEX,
        TABLE,
        ["subject_id"],
        unique=True,
        postgresql_where=sa.text("status = 'APPROVED'"),
    )
    op.drop_column(TABLE, "superseded_by_id")
    op.drop_column(TABLE, "superseded_at")
    op.drop_column(TABLE, "replaces_registration_id")
