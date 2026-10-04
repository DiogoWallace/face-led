"""Comandos administrativos.

python -m app.cli create-tenant --name "Projeto X" --slug projeto-x
python -m app.cli generate-encryption-key
python -m app.cli download-models [--dir models]
python -m app.cli set-webhook --slug projeto-x --url https://... [--rotate-secret]
python -m app.cli disable-webhook --slug projeto-x
"""

import argparse
import asyncio
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.application.use_cases.authenticate_tenant import generate_api_key, hash_api_key
from app.config import get_settings
from app.container import SystemClock
from app.domain.entities import Tenant, TenantStatus
from app.infrastructure.database.session import SqlAlchemyUnitOfWork, create_engine
from app.infrastructure.security.template_cipher import AesGcmTemplateCipher, generate_key


async def create_tenant(name: str, slug: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{1,98}[a-z0-9]", slug):
        sys.exit("slug inválido (use minúsculas, números e hífen)")
    engine = create_engine(get_settings().database_url.get_secret_value())
    api_key = generate_api_key()
    now = datetime.now(UTC)
    tenant = Tenant(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        status=TenantStatus.ACTIVE,
        api_key_hash=hash_api_key(api_key),
        created_at=now,
        updated_at=now,
    )
    try:
        async with SqlAlchemyUnitOfWork(async_sessionmaker(engine)) as uow:
            await uow.tenants.add(tenant)
            await uow.commit()
    finally:
        await engine.dispose()
    # A chave aparece uma única vez; apenas o hash é persistido.
    print(f"tenant_id={tenant.id}")
    print(f"api_key={api_key}")


async def set_webhook(slug: str, url: str, rotate_secret: bool) -> None:
    from app.application.use_cases.webhooks import ConfigureTenantWebhook, InvalidWebhookUrl

    settings = get_settings()
    engine = create_engine(settings.database_url.get_secret_value())
    use_case = ConfigureTenantWebhook(
        uow_factory=lambda: SqlAlchemyUnitOfWork(async_sessionmaker(engine)),
        cipher=AesGcmTemplateCipher(settings.template_encryption_key.get_secret_value()),
        clock=SystemClock(),
    )
    try:
        secret = await use_case.execute(
            slug=slug,
            url=url,
            rotate_secret=rotate_secret,
            allow_http=settings.app_env in ("local", "test"),
        )
    except (InvalidWebhookUrl, LookupError) as error:
        sys.exit(str(error))
    finally:
        await engine.dispose()
    print(f"webhook configurado para {slug}")
    if secret:
        # Mostrado uma única vez; o banco guarda só a versão cifrada.
        print(f"webhook_secret={secret}")
    else:
        print("segredo mantido (use --rotate-secret para gerar outro)")


async def disable_webhook(slug: str) -> None:
    from app.application.use_cases.webhooks import DisableTenantWebhook

    engine = create_engine(get_settings().database_url.get_secret_value())
    use_case = DisableTenantWebhook(
        uow_factory=lambda: SqlAlchemyUnitOfWork(async_sessionmaker(engine)),
        clock=SystemClock(),
    )
    try:
        await use_case.execute(slug=slug)
    except LookupError as error:
        sys.exit(str(error))
    finally:
        await engine.dispose()
    print(f"webhook desligado para {slug}; entregas pendentes serão encerradas")


def download_models(models_dir: Path) -> None:
    # Import tardio: só este comando precisa de OpenCV/numpy.
    from app.infrastructure.biometric.opencv.models import ALL_MODELS, download_model

    for model in ALL_MODELS:
        downloaded = download_model(models_dir, model)
        state = "baixado" if downloaded else "já presente"
        print(f"{model.filename}: {state}, SHA-256 conferido ({model.license})")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-tenant")
    create.add_argument("--name", required=True)
    create.add_argument("--slug", required=True)
    sub.add_parser("generate-encryption-key")
    webhook = sub.add_parser("set-webhook")
    webhook.add_argument("--slug", required=True)
    webhook.add_argument("--url", required=True)
    webhook.add_argument("--rotate-secret", action="store_true")
    disable = sub.add_parser("disable-webhook")
    disable.add_argument("--slug", required=True)
    models = sub.add_parser("download-models")
    models.add_argument("--dir", default=os.environ.get("BIOMETRIC_MODELS_DIR", "models"))
    args = parser.parse_args()

    if args.command == "create-tenant":
        asyncio.run(create_tenant(args.name, args.slug))
    elif args.command == "generate-encryption-key":
        print(generate_key())
    elif args.command == "set-webhook":
        asyncio.run(set_webhook(args.slug, args.url, args.rotate_secret))
    elif args.command == "disable-webhook":
        asyncio.run(disable_webhook(args.slug))
    elif args.command == "download-models":
        download_models(Path(args.dir))


if __name__ == "__main__":
    main()
