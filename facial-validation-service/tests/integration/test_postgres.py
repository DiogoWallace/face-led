import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.domain.entities import (
    FaceRegistration,
    Verification,
    WebhookDelivery,
    WebhookEventType,
)
from app.domain.exceptions import DuplicateIdempotencyKey
from app.domain.value_objects import ProcessStatus, RejectionReason

pytestmark = pytest.mark.integration


async def test_migrations_create_expected_tables(engine):
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert {
        "tenants",
        "subjects",
        "face_registrations",
        "liveness_sessions",
        "verifications",
        "verification_events",
    } <= tables


def registration(tenant, subject) -> FaceRegistration:
    return FaceRegistration.create(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        subject_id=subject.id,
        capture_object_key="k",
        now=datetime.now(UTC),
    )


def verification(tenant, subject, key="idem-0001") -> Verification:
    now = datetime.now(UTC)
    return Verification.create(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        subject_id=subject.id,
        idempotency_key=key,
        capture_object_key="k",
        expires_at=now + timedelta(minutes=5),
        now=now,
    )


async def test_registration_roundtrip_and_active_lookup(uow_factory, tenant_and_subject):
    tenant, subject = tenant_and_subject
    reg = registration(tenant, subject)
    reg.start_processing(datetime.now(UTC))
    reg.approve(template=b"\x01cipher", model_name="m", model_version="1", now=datetime.now(UTC))
    async with uow_factory() as uow:
        await uow.face_registrations.save(reg)
        await uow.commit()
    async with uow_factory() as uow:
        active = await uow.face_registrations.get_active_for_subject(tenant.id, subject.id)
        other_tenant = await uow.face_registrations.get_active_for_subject(uuid.uuid4(), subject.id)
    assert active is not None and active.template == b"\x01cipher"
    assert other_tenant is None


async def test_only_one_active_registration_per_subject(uow_factory, tenant_and_subject):
    tenant, subject = tenant_and_subject
    first, second = registration(tenant, subject), registration(tenant, subject)
    for reg in (first, second):
        reg.start_processing(datetime.now(UTC))
        reg.approve(template=b"x", model_name="m", model_version="1", now=datetime.now(UTC))
    async with uow_factory() as uow:
        await uow.face_registrations.save(first)
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.face_registrations.save(second)


async def test_approved_requires_template(uow_factory, tenant_and_subject):
    tenant, subject = tenant_and_subject
    reg = registration(tenant, subject)
    reg.status = ProcessStatus.APPROVED
    async with uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.face_registrations.save(reg)


async def test_idempotency_key_unique_per_tenant(uow_factory, tenant_and_subject):
    tenant, subject = tenant_and_subject
    async with uow_factory() as uow:
        await uow.verifications.save(verification(tenant, subject))
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(DuplicateIdempotencyKey):
            await uow.verifications.save(verification(tenant, subject))


async def test_verification_scoped_by_tenant(uow_factory, tenant_and_subject):
    tenant, subject = tenant_and_subject
    v = verification(tenant, subject)
    async with uow_factory() as uow:
        await uow.verifications.save(v)
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.verifications.get_for_tenant(tenant.id, v.id) is not None
        assert await uow.verifications.get_for_tenant(uuid.uuid4(), v.id) is None
        assert (await uow.verifications.get_by_idempotency_key(tenant.id, "idem-0001")).id == v.id


async def test_quality_issues_and_registration_lookups(uow_factory, tenant_and_subject):
    tenant, subject = tenant_and_subject
    base = datetime.now(UTC)
    rejected = registration(tenant, subject)
    rejected.created_at = rejected.updated_at = base
    rejected.start_processing(base)
    rejected.reject(RejectionReason.LOW_QUALITY, base)
    rejected.record_quality(("BLURRY", "TOO_DARK"))
    pending = registration(tenant, subject)
    pending.created_at = pending.updated_at = base + timedelta(seconds=1)
    async with uow_factory() as uow:
        await uow.face_registrations.save(rejected)
        await uow.face_registrations.save(pending)
        await uow.commit()

    async with uow_factory() as uow:
        stored = await uow.face_registrations.get_for_tenant(tenant.id, rejected.id)
        hidden = await uow.face_registrations.get_for_tenant(uuid.uuid4(), rejected.id)
        latest = await uow.face_registrations.get_latest_for_subject(tenant.id, subject.id)
    assert stored.quality_issues == ("BLURRY", "TOO_DARK")
    assert hidden is None
    assert latest.id == pending.id and latest.quality_issues is None


async def test_verification_quality_issues_roundtrip(uow_factory, tenant_and_subject):
    tenant, subject = tenant_and_subject
    v = verification(tenant, subject, key="idem-quality")
    v.start_processing(datetime.now(UTC))
    v.reject(RejectionReason.MULTIPLE_FACES, datetime.now(UTC))
    v.record_quality(("MULTIPLE_FACES",))
    async with uow_factory() as uow:
        await uow.verifications.save(v)
        await uow.commit()
    async with uow_factory() as uow:
        stored = await uow.verifications.get_for_tenant(tenant.id, v.id)
    assert stored.quality_issues == ("MULTIPLE_FACES",)


def approved(tenant, subject, at: datetime) -> FaceRegistration:
    r = registration(tenant, subject)
    r.created_at = r.updated_at = at
    r.start_processing(at)
    r.approve(template=b"\x01cipher", model_name="m", model_version="1", now=at)
    return r


async def test_reenrollment_swap_respects_constraints(uow_factory, tenant_and_subject):
    tenant, subject = tenant_and_subject
    now = datetime.now(UTC)
    old = approved(tenant, subject, now)
    async with uow_factory() as uow:
        await uow.face_registrations.save(old)
        await uow.commit()

    new = registration(tenant, subject)
    new.replaces_registration_id = old.id
    async with uow_factory() as uow:
        await uow.face_registrations.save(new)
        await uow.commit()

    # A troca na mesma transação, na ordem do worker: aposenta a antiga, aprova a nova.
    new.start_processing(now)
    new.approve(template=b"\x02cipher", model_name="m", model_version="1", now=now)
    old.supersede(by=new.id, now=now)
    async with uow_factory() as uow:
        await uow.face_registrations.save(old)
        await uow.face_registrations.save(new)
        await uow.commit()

    async with uow_factory() as uow:
        active = await uow.face_registrations.get_active_for_subject(tenant.id, subject.id)
        stored_old = await uow.face_registrations.get_for_tenant(tenant.id, old.id)
    assert active.id == new.id and active.replaces_registration_id == old.id
    assert stored_old.template is None and stored_old.superseded_by_id == new.id


async def test_superseded_reference_cannot_keep_template(uow_factory, tenant_and_subject):
    tenant, subject = tenant_and_subject
    r = approved(tenant, subject, datetime.now(UTC))
    r.superseded_at, r.superseded_by_id = datetime.now(UTC), r.id  # template NÃO apagado
    with pytest.raises(IntegrityError):
        async with uow_factory() as uow:
            await uow.face_registrations.save(r)
            await uow.commit()


async def test_two_active_references_still_impossible(uow_factory, tenant_and_subject):
    tenant, subject = tenant_and_subject
    now = datetime.now(UTC)
    with pytest.raises(IntegrityError):
        async with uow_factory() as uow:
            await uow.face_registrations.save(approved(tenant, subject, now))
            await uow.face_registrations.save(approved(tenant, subject, now))
            await uow.commit()


async def test_tenant_webhook_roundtrip_and_completeness(uow_factory, tenant_and_subject):
    tenant, _ = tenant_and_subject
    tenant.configure_webhook(
        url="https://consumidor.example/hook",
        encrypted_secret=b"\x01cifrado",
        now=datetime.now(UTC),
    )
    async with uow_factory() as uow:
        await uow.tenants.save(tenant)
        await uow.commit()
    async with uow_factory() as uow:
        stored = await uow.tenants.get(tenant.id)
        by_slug = await uow.tenants.get_by_slug(tenant.slug)
    assert stored.webhook_enabled and stored.webhook_secret == b"\x01cifrado"
    assert by_slug.id == tenant.id

    tenant.webhook_secret = None  # URL sem segredo: o banco recusa
    with pytest.raises(IntegrityError):
        async with uow_factory() as uow:
            await uow.tenants.save(tenant)
            await uow.commit()


async def test_claim_due_skips_rows_locked_by_another_worker(uow_factory, tenant_and_subject):
    tenant, _ = tenant_and_subject
    now = datetime.now(UTC)
    deliveries = [
        WebhookDelivery.create(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            event_type=WebhookEventType.VERIFICATION_COMPLETED,
            resource_id=uuid.uuid4(),
            now=now - timedelta(seconds=i),
        )
        for i in range(3)
    ]
    async with uow_factory() as uow:
        for d in deliveries:
            await uow.webhook_deliveries.add(d)
        await uow.commit()

    lease = timedelta(minutes=2)
    async with uow_factory() as first, uow_factory() as second:
        claimed_a = await first.webhook_deliveries.claim_due(now, lease, 2)  # trava 2 linhas
        claimed_b = await second.webhook_deliveries.claim_due(now, lease, 10)  # pula as travadas
        assert len(claimed_a) == 2 and len(claimed_b) == 1
        assert {d.id for d in claimed_a}.isdisjoint({d.id for d in claimed_b})
        await first.commit()
        await second.commit()

    async with uow_factory() as uow:
        assert await uow.webhook_deliveries.claim_due(now, lease, 10) == []  # reservadas
        again = await uow.webhook_deliveries.claim_due(now + lease, lease, 10)  # reserva venceu
    assert len(again) == 3 and all(d.attempts == 2 for d in again)
