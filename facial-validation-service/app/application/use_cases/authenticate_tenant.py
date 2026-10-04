import hashlib
import secrets

from app.application.ports import UnitOfWorkFactory
from app.domain.entities import Tenant
from app.domain.exceptions import AuthenticationFailed

API_KEY_PREFIX = "fvs_"


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    # Chaves têm 256 bits aleatórios; SHA-256 basta para lookup sem guardar a chave em claro.
    return hashlib.sha256(api_key.encode()).hexdigest()


class AuthenticateTenant:
    """Autenticação sistema-a-sistema provisória por API key (ver pendências no ADR-002)."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, api_key: str | None) -> Tenant:
        if not api_key or not api_key.startswith(API_KEY_PREFIX) or len(api_key) > 256:
            raise AuthenticationFailed("credencial ausente ou inválida")
        async with self._uow_factory() as uow:
            tenant = await uow.tenants.get_by_api_key_hash(hash_api_key(api_key))
        if tenant is None or not tenant.is_active:
            raise AuthenticationFailed("credencial ausente ou inválida")
        return tenant
