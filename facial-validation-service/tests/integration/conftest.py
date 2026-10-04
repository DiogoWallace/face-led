"""Infra real do compose. Usa banco, db do Redis e bucket exclusivos de teste."""

import os
import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.entities import Subject, Tenant, TenantStatus
from app.infrastructure.database.session import SqlAlchemyUnitOfWork, create_engine
from app.infrastructure.storage.s3 import S3CaptureStorage

pytestmark = pytest.mark.integration

TABLES = (
    "verification_events, verifications, liveness_sessions, face_registrations, subjects, tenants"
)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} não definida (execute dentro do compose: make test-integration)")
    return value


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _required("TEST_DATABASE_URL")
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return url


@pytest.fixture
async def engine(database_url):
    engine = create_engine(database_url)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
def uow_factory(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


@pytest.fixture
async def tenant_and_subject(uow_factory):
    now = datetime.now(UTC)
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Integração",
        slug=f"it-{uuid.uuid4().hex[:8]}",
        status=TenantStatus.ACTIVE,
        api_key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        created_at=now,
        updated_at=now,
    )
    subject = Subject(
        id=uuid.uuid4(), tenant_id=tenant.id, external_id="user-1", created_at=now, updated_at=now
    )
    async with uow_factory() as uow:
        await uow.tenants.add(tenant)
        await uow.subjects.add(subject)
        await uow.commit()
    return tenant, subject


@pytest.fixture
async def storage():
    s = S3CaptureStorage(
        bucket=os.environ.get("TEST_S3_BUCKET", "facial-validation-test"),
        endpoint_url=_required("S3_ENDPOINT"),
        region=os.environ.get("S3_REGION", "us-east-1"),
        access_key=_required("S3_ACCESS_KEY"),
        secret_key=_required("S3_SECRET_KEY"),
    )
    await s.ensure_bucket()
    return s


@pytest.fixture
def redis_url() -> str:
    return _required("TEST_REDIS_URL")
