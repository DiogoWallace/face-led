"""Fila de processamento assíncrono sobre Redis usando arq (ver ADR-005)."""

import logging
from typing import Any
from uuid import UUID

from arq.connections import ArqRedis, RedisSettings, create_pool
from redis.exceptions import RedisError

from app.application.ports import TaskQueueError

PROCESS_FACE_REGISTRATION = "process_face_registration"
PROCESS_VERIFICATION = "process_verification"
DELIVER_WEBHOOKS = "deliver_webhooks"
# Só em APP_ENV=local: medição de um quadro para a captura guiada de /dev/liveness.
OBSERVE_LIVENESS_FRAME = "observe_liveness_frame"

logger = logging.getLogger(__name__)


def redis_settings(redis_url: str) -> RedisSettings:
    return RedisSettings.from_dsn(redis_url)


async def create_queue_pool(redis_url: str) -> ArqRedis:
    return await create_pool(redis_settings(redis_url))


class ArqTaskQueue:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def _enqueue(self, function: str, job_id: str, entity_id: UUID) -> None:
        try:
            # _job_id deduplica reenvios do mesmo item enquanto o job existir.
            await self._pool.enqueue_job(function, str(entity_id), _job_id=job_id)
        except (RedisError, OSError) as error:
            raise TaskQueueError("fila indisponível") from error

    async def enqueue_face_registration(self, registration_id: UUID) -> None:
        await self._enqueue(PROCESS_FACE_REGISTRATION, f"reg:{registration_id}", registration_id)

    async def enqueue_verification(self, verification_id: UUID) -> None:
        await self._enqueue(PROCESS_VERIFICATION, f"ver:{verification_id}", verification_id)

    async def enqueue_webhook_delivery(self, delivery_id: UUID) -> None:
        # O job varre tudo o que estiver vencido (inclusive esta entrega).
        await self._enqueue(DELIVER_WEBHOOKS, f"whk:{delivery_id}", delivery_id)


class ArqFrameObserver:
    """Mede um quadro no worker e espera a resposta (página /dev/liveness, SÓ em local).

    A API não carrega modelo (invariante 11): o quadro vai ao worker pelo arq e a
    resposta volta pelo resultado do job, guardado no Redis por poucos segundos.
    Devolve None quando o worker não responde a tempo ou falha.
    """

    def __init__(self, pool: ArqRedis, timeout_seconds: float = 3.0) -> None:
        self._pool = pool
        self._timeout = timeout_seconds

    async def observe(self, content: bytes, content_type: str) -> dict[str, Any] | None:
        try:
            job = await self._pool.enqueue_job(OBSERVE_LIVENESS_FRAME, content, content_type)
        except (RedisError, OSError) as error:
            raise TaskQueueError("fila indisponível") from error
        if job is None:
            return None
        try:
            return await job.result(timeout=self._timeout, poll_delay=0.05)
        except TimeoutError:
            return None
        except Exception:  # o job falhou no worker (ex.: imagem ilegível)
            logger.warning("liveness_frame_observation_failed")
            return None
