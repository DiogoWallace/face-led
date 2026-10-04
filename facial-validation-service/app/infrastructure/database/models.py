from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TZ = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class TenantModel(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20))
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Webhook (ADR-009). O segredo é guardado CIFRADO (AES-GCM, AAD = tenant).
    webhook_url: Mapped[str | None] = mapped_column(String(2048))
    webhook_secret: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(TZ)
    updated_at: Mapped[datetime] = mapped_column(TZ)


class SubjectModel(Base):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("tenant_id", "external_id", name="uq_subjects_tenant_ext"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    external_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(TZ)
    updated_at: Mapped[datetime] = mapped_column(TZ)


class FaceRegistrationModel(Base):
    __tablename__ = "face_registrations"
    __table_args__ = (
        Index("ix_face_registrations_tenant_subject", "tenant_id", "subject_id"),
        Index(
            "uq_face_registrations_liveness_challenge",
            "liveness_challenge_id",
            unique=True,
            postgresql_where=text("liveness_challenge_id IS NOT NULL"),
        ),
        # No máximo uma referência ativa por subject (substituídas não contam, ADR-008).
        Index(
            "uq_face_registrations_active_subject",
            "subject_id",
            unique=True,
            postgresql_where=text("status = 'APPROVED' AND superseded_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(64))
    capture_object_key: Mapped[str | None] = mapped_column(String(512))
    model_name: Mapped[str | None] = mapped_column(String(100))
    model_version: Mapped[str | None] = mapped_column(String(100))
    # Template cifrado (AES-GCM). Formato do embedding em claro depende do motor (ADR-003).
    template: Mapped[bytes | None] = mapped_column(LargeBinary)
    # Códigos do QualityGate; NULL = qualidade não avaliada.
    quality_issues: Mapped[list[str] | None] = mapped_column(JSONB)
    # Recadastro (ADR-008).
    replaces_registration_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("face_registrations.id", ondelete="RESTRICT")
    )
    superseded_at: Mapped[datetime | None] = mapped_column(TZ)
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("face_registrations.id", ondelete="RESTRICT")
    )
    liveness_challenge_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("liveness_challenges.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(TZ)
    updated_at: Mapped[datetime] = mapped_column(TZ)


class LivenessSessionModel(Base):
    __tablename__ = "liveness_sessions"
    __table_args__ = (Index("ix_liveness_sessions_reference", "reference_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id", ondelete="RESTRICT"))
    purpose: Mapped[str] = mapped_column(String(20))
    reference_id: Mapped[UUID]
    provider: Mapped[str] = mapped_column(String(100))
    verdict: Mapped[str | None] = mapped_column(String(20))
    score: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZ)
    completed_at: Mapped[datetime | None] = mapped_column(TZ)


class VerificationModel(Base):
    __tablename__ = "verifications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_verifications_idempotency"),
        Index("ix_verifications_tenant_subject", "tenant_id", "subject_id"),
        Index(
            "uq_verifications_liveness_challenge",
            "liveness_challenge_id",
            unique=True,
            postgresql_where=text("liveness_challenge_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id", ondelete="RESTRICT"))
    face_registration_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("face_registrations.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(64))
    similarity: Mapped[float | None] = mapped_column(Float)
    policy_version: Mapped[str | None] = mapped_column(String(50))
    capture_object_key: Mapped[str | None] = mapped_column(String(512))
    quality_issues: Mapped[list[str] | None] = mapped_column(JSONB)
    liveness_challenge_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("liveness_challenges.id", ondelete="RESTRICT")
    )
    expires_at: Mapped[datetime] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ)
    updated_at: Mapped[datetime] = mapped_column(TZ)


class VerificationEventModel(Base):
    __tablename__ = "verification_events"
    __table_args__ = (Index("ix_verification_events_aggregate", "aggregate_id", "occurred_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    aggregate_type: Mapped[str] = mapped_column(String(50))
    aggregate_id: Mapped[UUID]
    event_type: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(TZ)


class WebhookDeliveryModel(Base):
    """Outbox do webhook (ADR-009): só tipo do evento e id do recurso, sem dado pessoal."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index(
            "ix_webhook_deliveries_due",
            "next_attempt_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index("ix_webhook_deliveries_resource", "resource_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    event_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer)
    next_attempt_at: Mapped[datetime] = mapped_column(TZ)
    last_attempt_at: Mapped[datetime | None] = mapped_column(TZ)
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(64))
    delivered_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ)
    updated_at: Mapped[datetime] = mapped_column(TZ)


class LivenessChallengeModel(Base):
    """Sessão de desafio de liveness (ADR-010): uso único, com prazo."""

    __tablename__ = "liveness_challenges"
    __table_args__ = (Index("ix_liveness_challenges_tenant", "tenant_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    # id do consumidor (como em subjects): o desafio vale para o subject pedido.
    external_subject_id: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(20))
    steps: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(TZ)
    used_at: Mapped[datetime | None] = mapped_column(TZ)
    used_by_id: Mapped[UUID | None]
    frame_keys: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TZ)
    updated_at: Mapped[datetime] = mapped_column(TZ)
