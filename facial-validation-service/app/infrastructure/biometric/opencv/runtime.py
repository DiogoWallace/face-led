"""Execução da inferência OpenCV fora do event loop, com instância por thread.

Fatos do POC (docs/biometric-poc-results.md):
- um `FaceDetectorYN` compartilhado entre threads derrubou o processo com
  segfault (código 139);
- o OpenCV já usa vários núcleos numa única chamada, e mais threads por
  processo quase não aumentam a vazão.

Por isso: um `ThreadPoolExecutor` dedicado, e cada thread carrega a SUA
instância de cada modelo no initializer (`threading.local`). Nenhuma instância
é global ou compartilhada. Escalar = mais réplicas do worker.

O runtime só carrega modelos quando `start()` é chamado — o worker faz isso no
`on_startup`; a API nunca chama, e um adapter usado sem runtime iniciado falha
com BiometricProviderError (vira ERROR/PROVIDER_FAILURE).
"""

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.application.ports import BiometricProviderError
from app.infrastructure.biometric.opencv.models import SFACE, YUNET, ModelFile, verify_model

# Parâmetros fixos do YuNet, os mesmos do POC (poc/scripts/engines.py).
YUNET_INPUT_SIZE = (320, 320)  # redefinido a cada imagem com setInputSize
YUNET_NMS_THRESHOLD = 0.3
YUNET_TOP_K = 5000
_STARTUP_TIMEOUT_SECONDS = 120


class OpenCVRuntime:
    def __init__(
        self,
        *,
        models_dir: Path,
        inference_threads: int,
        opencv_threads: int | None,
        detector_score_threshold: float,
    ) -> None:
        if inference_threads < 1:
            raise ValueError("BIOMETRIC_INFERENCE_THREADS deve ser >= 1")
        self._models_dir = models_dir
        self._inference_threads = inference_threads
        self._opencv_threads = opencv_threads
        self._detector_score_threshold = detector_score_threshold
        self._required: set[ModelFile] = set()
        self._in_use = False
        self._executor: ThreadPoolExecutor | None = None
        self._local = threading.local()

    @property
    def started(self) -> bool:
        return self._executor is not None

    @property
    def required_models(self) -> tuple[str, ...]:
        return tuple(sorted(m.filename for m in self._required))

    def require(self, *models: ModelFile) -> None:
        """Registra um componente usuário do runtime e os modelos de que ele precisa.

        Sem modelos (ex.: o medidor de qualidade) o executor sobe do mesmo jeito.
        """
        if self.started:
            raise RuntimeError("modelos devem ser declarados antes de start()")
        self._in_use = True
        self._required.update(models)

    async def start(self) -> None:
        """Verifica o SHA-256 dos modelos e carrega uma instância em cada thread."""
        if self.started or not self._in_use:
            return
        paths = await asyncio.to_thread(
            lambda: {model: verify_model(self._models_dir, model) for model in self._required}
        )
        if self._opencv_threads is not None:
            cv2.setNumThreads(self._opencv_threads)

        executor = ThreadPoolExecutor(
            max_workers=self._inference_threads,
            thread_name_prefix="opencv-inference",
            initializer=self._load_thread_models,
            initargs=(paths,),
        )
        # A barreira prende cada tarefa até todas as threads existirem: assim o
        # executor cria TODAS agora, e uma falha de carga impede o worker de subir.
        barrier = threading.Barrier(self._inference_threads, timeout=_STARTUP_TIMEOUT_SECONDS)
        futures = [executor.submit(barrier.wait) for _ in range(self._inference_threads)]
        try:
            await asyncio.gather(*(asyncio.wrap_future(f) for f in futures))
        except BaseException:
            barrier.abort()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        self._executor = executor

    async def close(self) -> None:
        executor, self._executor = self._executor, None
        if executor is not None:
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)

    async def run[T](self, function: Callable[..., T], *args: Any) -> T:
        if self._executor is None:
            raise BiometricProviderError(
                "runtime OpenCV não iniciado (os modelos só são carregados pelo worker)"
            )
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, function, *args)
        except cv2.error as error:
            # A mensagem do OpenCV descreve o código, nunca o conteúdo da imagem.
            raise BiometricProviderError("falha na inferência OpenCV") from error

    # --- executado dentro das threads do executor ---

    def _load_thread_models(self, paths: dict[ModelFile, Path]) -> None:
        if YUNET in paths:
            self._local.detector = cv2.FaceDetectorYN.create(
                str(paths[YUNET]),
                "",
                YUNET_INPUT_SIZE,
                self._detector_score_threshold,
                YUNET_NMS_THRESHOLD,
                YUNET_TOP_K,
            )
        if SFACE in paths:
            self._local.recognizer = cv2.FaceRecognizerSF.create(str(paths[SFACE]), "")

    def thread_detector(self) -> Any:
        detector = getattr(self._local, "detector", None)
        if detector is None:
            raise BiometricProviderError("YuNet não carregado nesta thread")
        return detector

    def thread_recognizer(self) -> Any:
        recognizer = getattr(self._local, "recognizer", None)
        if recognizer is None:
            raise BiometricProviderError("SFace não carregado nesta thread")
        return recognizer


def decode_image(content: bytes) -> np.ndarray:
    """JPEG/PNG → BGR. IMREAD_COLOR aplica a orientação EXIF."""
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise BiometricProviderError("imagem não decodificável")
    return image
