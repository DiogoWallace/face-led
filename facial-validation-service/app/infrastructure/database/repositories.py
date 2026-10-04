from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import (
    FaceRegistration,
    LivenessChallenge,
    LivenessChallengeStatus,
    LivenessPurpose,
    LivenessSession,
    Subject,
    Tenant,
    TenantStatus,
    Verification,
    VerificationEvent,
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEventType,
)
from app.domain.exceptions import DuplicateIdempotencyKey
from app.domain.value_objects import ProcessStatus
from app.infrastructure.database.models import (
    FaceRegistrationModel,
    LivenessChallengeModel,
    LivenessSessionModel,
    SubjectModel,
    TenantModel,
    VerificationEventModel,
    VerificationModel,
    WebhookDeliveryModel,
)


def _tenant(m: TenantModel) -> Tenant:
    return Tenant(
        id=m.id,
        name=m.name,
        slug=m.slug,
        status=TenantStatus(m.status),
        api_key_hash=m.api_key_hash,
        webhook_url=m.webhook_url,
        webhook_secret=m.webhook_secret,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _subject(m: SubjectModel) -> Subject:
    return Subject(
        id=m.id,
        tenant_id=m.tenant_id,
        external_id=m.external_id,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _registration(m: FaceRegistrationModel) -> FaceRegistration:
    return FaceRegistration(
        id=m.id,
        tenant_id=m.tenant_id,
        subject_id=m.subject_id,
        status=ProcessStatus(m.status),
        reason=m.reason,
        capture_object_key=m.capture_object_key,
        model_name=m.model_name,
        model_version=m.model_version,
        template=m.template,
        quality_issues=_issues(m.quality_issues),
        replaces_registration_id=m.replaces_registration_id,
        superseded_at=m.superseded_at,
        superseded_by_id=m.superseded_by_id,
        liveness_challenge_id=m.liveness_challenge_id,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _verification(m: VerificationModel) -> Verification:
    return Verification(
        id=m.id,
        tenant_id=m.tenant_id,
        subject_id=m.subject_id,
        status=ProcessStatus(m.status),
        reason=m.reason,
        idempotency_key=m.idempotency_key,
        capture_object_key=m.capture_object_key,
        expires_at=m.expires_at,
        face_registration_id=m.face_registration_id,
        policy_version=m.policy_version,
        similarity=m.similarity,
        quality_issues=_issues(m.quality_issues),
        liveness_challenge_id=m.liveness_challenge_id,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _issues(value: list[str] | None) -> tuple[str, ...] | None:
    return None if value is None else tuple(value)


def _issues_column(value: tuple[str, ...] | None) -> list[str] | None:
    return None if value is None else list(value)


class SqlTenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_api_key_hash(self, api_key_hash: str) -> Tenant | None:
        m = await self._s.scalar(
            select(TenantModel).where(TenantModel.api_key_hash == api_key_hash)
        )
        return _tenant(m) if m else None

    async def get(self, tenant_id: UUID) -> Tenant | None:
        m = await self._s.get(TenantModel, tenant_id, populate_existing=True)
        return _tenant(m) if m else None

    async def get_by_slug(self, slug: str) -> Tenant | None:
        m = await self._s.scalar(select(TenantModel).where(TenantModel.slug == slug))
        return _tenant(m) if m else None

    async def add(self, tenant: Tenant) -> None:
        await self.save(tenant)

    async def save(self, tenant: Tenant) -> None:
        m = await self._s.get(TenantModel, tenant.id) or TenantModel(id=tenant.id)
        m.name, m.slug, m.status = tenant.name, tenant.slug, tenant.status.value
        m.api_key_hash = tenant.api_key_hash
        m.webhook_url, m.webhook_secret = tenant.webhook_url, tenant.webhook_secret
        m.created_at, m.updated_at = tenant.created_at, tenant.updated_at
        self._s.add(m)
        await self._s.flush()


class SqlSubjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_external_id(self, tenant_id: UUID, external_id: str) -> Subject | None:
        m = await self._s.scalar(
            select(SubjectModel).where(
                SubjectModel.tenant_id == tenant_id, SubjectModel.external_id == external_id
            )
        )
        return _subject(m) if m else None

    async def get(self, tenant_id: UUID, subject_id: UUID) -> Subject | None:
        m = await self._s.scalar(
            select(SubjectModel).where(
                SubjectModel.tenant_id == tenant_id, SubjectModel.id == subject_id
            )
        )
        return _subject(m) if m else None

    async def add(self, subject: Subject) -> None:
        self._s.add(
            SubjectModel(
                id=subject.id,
                tenant_id=subject.tenant_id,
                external_id=subject.external_id,
                created_at=subject.created_at,
                updated_at=subject.updated_at,
            )
        )
        await self._s.flush()


class SqlFaceRegistrationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, registration_id: UUID) -> FaceRegistration | None:
        m = await self._s.get(FaceRegistrationModel, registration_id, populate_existing=True)
        return _registration(m) if m else None

    async def get_for_tenant(
        self, tenant_id: UUID, registration_id: UUID
    ) -> FaceRegistration | None:
        m = await self._s.scalar(
            select(FaceRegistrationModel).where(
                FaceRegistrationModel.tenant_id == tenant_id,
                FaceRegistrationModel.id == registration_id,
            )
        )
        return _registration(m) if m else None

    async def get_latest_for_subject(
        self, tenant_id: UUID, subject_id: UUID
    ) -> FaceRegistration | None:
        m = await self._s.scalar(
            select(FaceRegistrationModel)
            .where(
                FaceRegistrationModel.tenant_id == tenant_id,
                FaceRegistrationModel.subject_id == subject_id,
            )
            .order_by(FaceRegistrationModel.created_at.desc(), FaceRegistrationModel.id.desc())
            .limit(1)
        )
        return _registration(m) if m else None

    async def get_active_for_subject(
        self, tenant_id: UUID, subject_id: UUID
    ) -> FaceRegistration | None:
        m = await self._s.scalar(
            select(FaceRegistrationModel).where(
                FaceRegistrationModel.tenant_id == tenant_id,
                FaceRegistrationModel.subject_id == subject_id,
                FaceRegistrationModel.status == ProcessStatus.APPROVED.value,
                FaceRegistrationModel.superseded_at.is_(None),
            )
        )
        return _registration(m) if m else None

    async def save(self, r: FaceRegistration) -> None:
        m = await self._s.get(FaceRegistrationModel, r.id) or FaceRegistrationModel(id=r.id)
        m.tenant_id, m.subject_id = r.tenant_id, r.subject_id
        m.status, m.reason = r.status.value, r.reason
        m.capture_object_key = r.capture_object_key
        m.model_name, m.model_version, m.template = r.model_name, r.model_version, r.template
        m.quality_issues = _issues_column(r.quality_issues)
        m.replaces_registration_id = r.replaces_registration_id
        m.superseded_at, m.superseded_by_id = r.superseded_at, r.superseded_by_id
        m.liveness_challenge_id = r.liveness_challenge_id
        m.created_at, m.updated_at = r.created_at, r.updated_at
        self._s.add(m)
        await self._s.flush()


class SqlVerificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, verification_id: UUID) -> Verification | None:
        m = await self._s.get(VerificationModel, verification_id, populate_existing=True)
        return _verification(m) if m else None

    async def get_for_tenant(self, tenant_id: UUID, verification_id: UUID) -> Verification | None:
        m = await self._s.scalar(
            select(VerificationModel).where(
                VerificationModel.tenant_id == tenant_id, VerificationModel.id == verification_id
            )
        )
        return _verification(m) if m else None

    async def get_by_idempotency_key(
        self, tenant_id: UUID, idempotency_key: str
    ) -> Verification | None:
        m = await self._s.scalar(
            select(VerificationModel).where(
                VerificationModel.tenant_id == tenant_id,
                VerificationModel.idempotency_key == idempotency_key,
            )
        )
        return _verification(m) if m else None

    async def save(self, v: Verification) -> None:
        m = await self._s.get(VerificationModel, v.id) or VerificationModel(id=v.id)
        m.tenant_id, m.subject_id = v.tenant_id, v.subject_id
        m.face_registration_id = v.face_registration_id
        m.idempotency_key = v.idempotency_key
        m.status, m.reason = v.status.value, v.reason
        m.similarity, m.policy_version = v.similarity, v.policy_version
        m.capture_object_key, m.expires_at = v.capture_object_key, v.expires_at
        m.quality_issues = _issues_column(v.quality_issues)
        m.liveness_challenge_id = v.liveness_challenge_id
        m.created_at, m.updated_at = v.created_at, v.updated_at
        self._s.add(m)
        try:
            await self._s.flush()
        except IntegrityError as error:
            if "uq_verifications_idempotency" in str(error.orig):
                raise DuplicateIdempotencyKey("Idempotency-Key já utilizada") from None
            raise


class SqlLivenessSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, ls: LivenessSession) -> None:
        self._s.add(
            LivenessSessionModel(
                id=ls.id,
                tenant_id=ls.tenant_id,
                subject_id=ls.subject_id,
                purpose=ls.purpose.value,
                reference_id=ls.reference_id,
                provider=ls.provider,
                verdict=ls.verdict.value if ls.verdict else None,
                score=ls.score,
                detail=ls.detail,
                created_at=ls.created_at,
                completed_at=ls.completed_at,
            )
        )


class SqlVerificationEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, e: VerificationEvent) -> None:
        self._s.add(
            VerificationEventModel(
                id=e.id,
                tenant_id=e.tenant_id,
                aggregate_type=e.aggregate_type,
                aggregate_id=e.aggregate_id,
                event_type=e.event_type,
                data=e.data,
                occurred_at=e.occurred_at,
            )
        )


def _delivery(m: WebhookDeliveryModel) -> WebhookDelivery:
    return WebhookDelivery(
        id=m.id,
        tenant_id=m.tenant_id,
        event_type=WebhookEventType(m.event_type),
        resource_id=m.resource_id,
        status=WebhookDeliveryStatus(m.status),
        attempts=m.attempts,
        next_attempt_at=m.next_attempt_at,
        last_attempt_at=m.last_attempt_at,
        last_status_code=m.last_status_code,
        last_error=m.last_error,
        delivered_at=m.delivered_at,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _apply_delivery(m: WebhookDeliveryModel, d: WebhookDelivery) -> None:
    m.tenant_id, m.event_type, m.resource_id = d.tenant_id, d.event_type.value, d.resource_id
    m.status, m.attempts, m.next_attempt_at = d.status.value, d.attempts, d.next_attempt_at
    m.last_attempt_at, m.last_status_code = d.last_attempt_at, d.last_status_code
    m.last_error, m.delivered_at = d.last_error, d.delivered_at
    m.created_at, m.updated_at = d.created_at, d.updated_at


class SqlWebhookDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, delivery: WebhookDelivery) -> None:
        m = WebhookDeliveryModel(id=delivery.id)
        _apply_delivery(m, delivery)
        self._s.add(m)
        await self._s.flush()

    async def claim_due(self, now: datetime, lease: timedelta, limit: int) -> list[WebhookDelivery]:
        # SKIP LOCKED: duas varreduras (cron + atalho, ou duas réplicas) nunca pegam a
        # mesma linha; a reserva avança next_attempt_at antes do commit.
        rows = (
            await self._s.scalars(
                select(WebhookDeliveryModel)
                .where(
                    WebhookDeliveryModel.status == WebhookDeliveryStatus.PENDING.value,
                    WebhookDeliveryModel.next_attempt_at <= now,
                )
                .order_by(WebhookDeliveryModel.next_attempt_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        claimed = []
        for m in rows:
            delivery = _delivery(m)
            delivery.claim(now, lease)
            _apply_delivery(m, delivery)
            claimed.append(delivery)
        await self._s.flush()
        return claimed

    async def save(self, delivery: WebhookDelivery) -> None:
        m = await self._s.get(WebhookDeliveryModel, delivery.id) or WebhookDeliveryModel(
            id=delivery.id
        )
        _apply_delivery(m, delivery)
        self._s.add(m)
        await self._s.flush()


def _challenge(m: LivenessChallengeModel) -> LivenessChallenge:
    return LivenessChallenge(
        id=m.id,
        tenant_id=m.tenant_id,
        external_subject_id=m.external_subject_id,
        purpose=LivenessPurpose(m.purpose),
        steps=tuple(m.steps),
        status=LivenessChallengeStatus(m.status),
        expires_at=m.expires_at,
        used_at=m.used_at,
        used_by_id=m.used_by_id,
        frame_keys=tuple(m.frame_keys),
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class SqlLivenessChallengeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, challenge: LivenessChallenge) -> None:
        await self.save(challenge)

    async def get(self, challenge_id: UUID) -> LivenessChallenge | None:
        m = await self._s.get(LivenessChallengeModel, challenge_id)
        return _challenge(m) if m else None

    async def get_for_tenant(
        self, tenant_id: UUID, challenge_id: UUID, *, for_update: bool = False
    ) -> LivenessChallenge | None:
        query = select(LivenessChallengeModel).where(
            LivenessChallengeModel.tenant_id == tenant_id,
            LivenessChallengeModel.id == challenge_id,
        )
        if for_update:
            # Duas requisições com a mesma sessão: a segunda espera e vê USED.
            query = query.with_for_update()
        m = await self._s.scalar(query)
        return _challenge(m) if m else None

    async def save(self, c: LivenessChallenge) -> None:
        m = await self._s.get(LivenessChallengeModel, c.id) or LivenessChallengeModel(id=c.id)
        m.tenant_id, m.external_subject_id = c.tenant_id, c.external_subject_id
        m.purpose, m.steps, m.status = c.purpose.value, list(c.steps), c.status.value
        m.expires_at, m.used_at, m.used_by_id = c.expires_at, c.used_at, c.used_by_id
        m.frame_keys = list(c.frame_keys)
        m.created_at, m.updated_at = c.created_at, c.updated_at
        self._s.add(m)
        await self._s.flush()
