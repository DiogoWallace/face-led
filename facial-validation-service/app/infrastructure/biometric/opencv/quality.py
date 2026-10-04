"""FaceQualityAssessor com OpenCV: MEDE nitidez e brilho do rosto detectado.

Não decide nada: os limites ficam em `QualityRequirements` (domínio), vindos de
configuração e PENDING CALIBRATION. Tamanho absoluto e relativo do rosto o
QualityGate calcula da própria detecção.

Definição das medidas (versão `v1`; mudar a definição exige outro nome, porque
os limites calibrados valem para uma definição específica):

- região: a caixa do rosto, recortada aos limites da imagem;
- brilho: média dos tons de cinza da região, 0–255, na resolução original;
- nitidez: variância do Laplaciano dos tons de cinza da região redimensionada
  para 112×112 (a entrada do SFace). Na resolução original a medida depende
  do tamanho do rosto: a mesma textura deu 64 a 112 px e 2,4 a 900 px. Em
  112×112 ficou entre 61 e 64 nessa faixa, e o borrado (σ=2) seguiu ~5× menor.
  Rostos menores que 112 px dão nitidez menor (há menos detalhe real).

Região vazia (caixa toda fora da imagem) → medições `None`; com o critério
configurado, o QualityGate transforma isso em ERROR/QUALITY_MEASUREMENT_UNAVAILABLE.
"""

import cv2
import numpy as np

from app.domain.value_objects import BoundingBox, CaptureData, DetectedFace, QualityMeasurements
from app.infrastructure.biometric.opencv.runtime import OpenCVRuntime, decode_image

QUALITY_MEASUREMENT_VERSION = "v1"
SHARPNESS_SIDE = 112


def face_region(image: np.ndarray, box: BoundingBox) -> np.ndarray | None:
    height, width = image.shape[:2]
    x0, y0 = max(int(np.floor(box.x)), 0), max(int(np.floor(box.y)), 0)
    x1 = min(int(np.ceil(box.x + box.width)), width)
    y1 = min(int(np.ceil(box.y + box.height)), height)
    if x1 <= x0 or y1 <= y0:
        return None
    return image[y0:y1, x0:x1]


def measure_face(image: np.ndarray, box: BoundingBox) -> QualityMeasurements:
    region = face_region(image, box)
    if region is None:
        return QualityMeasurements()
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())

    shrinking = gray.shape[0] > SHARPNESS_SIDE or gray.shape[1] > SHARPNESS_SIDE
    normalized = cv2.resize(
        gray,
        (SHARPNESS_SIDE, SHARPNESS_SIDE),
        interpolation=cv2.INTER_AREA if shrinking else cv2.INTER_LINEAR,
    )
    sharpness = float(cv2.Laplacian(normalized, cv2.CV_64F).var())
    return QualityMeasurements(sharpness=round(sharpness, 2), brightness=round(brightness, 2))


class OpenCVQualityAssessor:
    name = f"opencv-quality/{QUALITY_MEASUREMENT_VERSION}"

    def __init__(self, runtime: OpenCVRuntime) -> None:
        runtime.require()  # não usa modelo, só o executor (decodificar e medir fora do loop)
        self._runtime = runtime

    async def measure(self, capture: CaptureData, face: DetectedFace) -> QualityMeasurements:
        return await self._runtime.run(self._measure, capture.content, face.box)

    def _measure(self, content: bytes, box: BoundingBox) -> QualityMeasurements:
        return measure_face(decode_image(content), box)
