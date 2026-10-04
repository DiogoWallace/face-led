"""Adapter de object storage S3 compatível (SeaweedFS local; AWS S3/compatível fora dele)."""

import asyncio
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.application.ports import CaptureStorageError
from app.domain.value_objects import CaptureData


class S3CaptureStorage:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None,
        region: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        self._bucket = bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=3,
                read_timeout=10,
            ),
        )

    async def _call(self, method: str, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(getattr(self._client, method), **kwargs)
        except (BotoCoreError, ClientError) as error:
            # Mensagem genérica: não propagar chaves ou detalhes do provedor.
            raise CaptureStorageError(f"falha no storage ({method})") from error

    async def put(self, key: str, capture: CaptureData) -> None:
        await self._call(
            "put_object",
            Bucket=self._bucket,
            Key=key,
            Body=capture.content,
            ContentType=capture.content_type,
        )

    async def get(self, key: str) -> CaptureData:
        response = await self._call("get_object", Bucket=self._bucket, Key=key)
        body = await asyncio.to_thread(response["Body"].read)
        return CaptureData(content=body, content_type=response.get("ContentType", ""))

    async def delete(self, key: str) -> None:
        await self._call("delete_object", Bucket=self._bucket, Key=key)

    async def temporary_url(self, key: str, expires_in_seconds: int) -> str:
        return await self._call(
            "generate_presigned_url",
            ClientMethod="get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in_seconds,
        )

    async def check(self) -> None:
        await self._call("head_bucket", Bucket=self._bucket)

    async def ensure_bucket(self) -> None:
        """Somente para ambiente local: cria o bucket privado se não existir."""
        try:
            await self.check()
        except CaptureStorageError:
            await self._call("create_bucket", Bucket=self._bucket)
