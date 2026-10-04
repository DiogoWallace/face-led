"""FaceEmbedder e FaceComparator com OpenCV SFace 2021dec.

Formato do template: 128 valores float32 little-endian (512 bytes), identificado
por `model_name="opencv-sface"` e `model_version="2021dec"`. Templates de outro
modelo não são comparáveis (ERROR/MODEL_MISMATCH).

O comparator devolve a similaridade de cosseno (maior = mais parecido), a mesma
de `FaceRecognizerSF.match(..., FR_COSINE)`. Não aplica threshold: quem decide
é a FaceMatchPolicy.
"""

import numpy as np

from app.application.ports import BiometricProviderError
from app.domain.value_objects import CaptureData, DetectedFace, FaceEmbedding
from app.infrastructure.biometric.opencv.models import SFACE
from app.infrastructure.biometric.opencv.runtime import OpenCVRuntime, decode_image
from app.infrastructure.biometric.opencv.yunet_format import yunet_row_from_face

SFACE_MODEL_NAME = "opencv-sface"
SFACE_MODEL_VERSION = "2021dec"
SFACE_DIMENSIONS = 128
_DTYPE = np.dtype("<f4")


def vector_to_bytes(vector: np.ndarray) -> bytes:
    flat = np.asarray(vector, dtype=_DTYPE).reshape(-1)
    if flat.size != SFACE_DIMENSIONS:
        raise BiometricProviderError("embedding SFace com dimensão inesperada")
    return flat.tobytes()


def vector_from_bytes(data: bytes) -> np.ndarray:
    if len(data) != SFACE_DIMENSIONS * _DTYPE.itemsize:
        raise BiometricProviderError("template SFace com tamanho inesperado")
    return np.frombuffer(data, dtype=_DTYPE).astype(np.float64)


def _is_sface(embedding: FaceEmbedding) -> bool:
    return (embedding.model_name, embedding.model_version) == (
        SFACE_MODEL_NAME,
        SFACE_MODEL_VERSION,
    )


class SFaceEmbedder:
    name = f"{SFACE_MODEL_NAME}/{SFACE_MODEL_VERSION}"

    def __init__(self, runtime: OpenCVRuntime) -> None:
        runtime.require(SFACE)
        self._runtime = runtime

    async def embed(self, capture: CaptureData, face: DetectedFace) -> FaceEmbedding:
        row = yunet_row_from_face(face)  # sem landmarks não há alinhamento: falha antes
        vector = await self._runtime.run(self._embed, capture.content, row)
        return FaceEmbedding(
            vector=vector, model_name=SFACE_MODEL_NAME, model_version=SFACE_MODEL_VERSION
        )

    def _embed(self, content: bytes, row: np.ndarray) -> bytes:
        image = decode_image(content)
        recognizer = self._runtime.thread_recognizer()
        aligned = recognizer.alignCrop(image, row)
        return vector_to_bytes(recognizer.feature(aligned))


class SFaceCosineComparator:
    """Cosseno em numpy: barato (128 valores), não precisa do modelo nem do executor."""

    name = f"{SFACE_MODEL_NAME}-cosine"

    async def similarity(self, probe: FaceEmbedding, reference: FaceEmbedding) -> float:
        if not (_is_sface(probe) and _is_sface(reference)):
            raise BiometricProviderError("o comparator SFace só compara templates opencv-sface")
        a, b = vector_from_bytes(probe.vector), vector_from_bytes(reference.vector)
        norms = float(np.linalg.norm(a) * np.linalg.norm(b))
        if norms == 0.0 or not np.isfinite(norms):
            raise BiometricProviderError("template SFace inválido (norma zero)")
        return float(np.dot(a, b) / norms)
