"""Worker assíncrono (arq). Executar: arq app.worker.WorkerSettings"""

import logging
from typing import Any, ClassVar
from uuid import UUID

from arq import cron
from arq.worker import func

from app.application.use_cases.liveness import observe_frame
from app.config import get_settings
from app.container import build_container
from app.domain.value_objects import CaptureData
from app.infrastructure.observability.logging import configure_logging
from app.infrastructure.queue.arq_queue import redis_settings

logger = logging.getLogger("app.worker")


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logging.getLogger("arq").propagate = False  # arq já tem handler próprio
    container = await build_container(settings)
    try:
        # Verifica o SHA-256 e carrega os modelos em cada thread de inferência.
        # Falha aqui impede o worker de subir (nunca processa sem o modelo certo).
        await container.start_inference()
    except BaseException:
        await container.close()
        raise
    ctx["container"] = container
    logger.info(
        "worker_startup",
        extra={
            "detector": settings.biometric_detector,
            "embedder": settings.biometric_embedder,
            "comparator": settings.biometric_comparator,
            "liveness": settings.liveness_provider,
            "models": list(container.loaded_models),
            "inference_threads": settings.biometric_inference_threads,
        },
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    # Ausente quando o startup falhou (ex.: modelo inválido); aí ele mesmo já fechou.
    container = ctx.pop("container", None)
    if container is not None:
        await container.close()


async def process_face_registration(ctx: dict[str, Any], registration_id: str) -> str | None:
    status = await ctx["container"].process_face_registration().execute(UUID(registration_id))
    return status.value if status else None


async def process_verification(ctx: dict[str, Any], verification_id: str) -> str | None:
    status = await ctx["container"].process_verification().execute(UUID(verification_id))
    return status.value if status else None


async def deliver_webhooks(ctx: dict[str, Any], _delivery_id: str | None = None) -> int:
    """Entrega os webhooks vencidos. Atalho pós-commit e varredura periódica usam o mesmo job.

    A varredura cobre o que o atalho perder (fila fora no commit) e as novas tentativas.
    """
    return await ctx["container"].deliver_webhooks().execute()


async def sweep_webhooks(ctx: dict[str, Any]) -> int:
    return await deliver_webhooks(ctx)


async def observe_liveness_frame(
    ctx: dict[str, Any], content: bytes, content_type: str
) -> dict[str, Any]:
    """Medida de um quadro para a captura guiada de /dev/liveness (só APP_ENV=local)."""
    detector = ctx["container"].pipeline.components.detector
    observation, detection = await observe_frame(
        detector, CaptureData(content=content, content_type=content_type)
    )
    w, h = detection.image_width, detection.image_height
    box = None
    if observation.face_count == 1:
        b = detection.faces[0].box
        # Caixa normalizada (0..1) só para desenhar a guia; nunca a imagem.
        box = [round(b.x / w, 4), round(b.y / h, 4), round(b.width / w, 4), round(b.height / h, 4)]
    return {
        "face_count": observation.face_count,
        "yaw": observation.yaw,
        "eye_distance": observation.eye_distance,
        "image_width": w,
        "image_height": h,
        "box": box,
    }


_LOCAL = get_settings().app_env == "local"
# O quadro viaja nos argumentos do job e o arq guarda os argumentos junto com o
# resultado: 10 s bastam para a API ler a resposta, sem manter biometria no Redis.
_DEV_FUNCTIONS = (
    [func(observe_liveness_frame, keep_result=10, max_tries=1, timeout=10)] if _LOCAL else []
)


class WorkerSettings:
    functions: ClassVar = [
        process_face_registration,
        process_verification,
        deliver_webhooks,
        *_DEV_FUNCTIONS,
    ]
    # A cada 30 s; unique=True (padrão do cron do arq) evita duas varreduras simultâneas.
    cron_jobs: ClassVar = [cron(sweep_webhooks, second={0, 30}, run_at_startup=True)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings(get_settings().redis_url.get_secret_value())
    max_jobs = 10
    job_timeout = 120
    max_tries = 3
    keep_result = 3600
    # Em local, a captura guiada espera a medida de cada quadro: busca jobs mais rápido.
    poll_delay = 0.05 if _LOCAL else 0.5
