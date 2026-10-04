from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.domain.exceptions import DuplicateIdempotencyKey
from app.infrastructure.database.repositories import (
    SqlFaceRegistrationRepository,
    SqlLivenessChallengeRepository,
    SqlLivenessSessionRepository,
    SqlSubjectRepository,
    SqlTenantRepository,
    SqlVerificationEventRepository,
    SqlVerificationRepository,
    SqlWebhookDeliveryRepository,
)


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)


async def check_database(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self.session = self._session_factory()
        self.tenants = SqlTenantRepository(self.session)
        self.subjects = SqlSubjectRepository(self.session)
        self.face_registrations = SqlFaceRegistrationRepository(self.session)
        self.verifications = SqlVerificationRepository(self.session)
        self.liveness_sessions = SqlLivenessSessionRepository(self.session)
        self.liveness_challenges = SqlLivenessChallengeRepository(self.session)
        self.events = SqlVerificationEventRepository(self.session)
        self.webhook_deliveries = SqlWebhookDeliveryRepository(self.session)
        return self

    async def __aexit__(self, *exc: object) -> None:
        try:
            await self.session.rollback()  # no-op quando já houve commit
        finally:
            await self.session.close()

    async def commit(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            if "uq_verifications_idempotency" in str(error.orig):
                raise DuplicateIdempotencyKey("Idempotency-Key já utilizada") from None
            raise

    async def rollback(self) -> None:
        await self.session.rollback()
