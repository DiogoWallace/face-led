"""Conversão entre a linha nativa do YuNet e os value objects do domínio.

Linha do `FaceDetectorYN.detect` (15 valores, float32):
    x, y, w, h,
    olho direito (x, y), olho esquerdo (x, y), ponta do nariz (x, y),
    canto direito da boca (x, y), canto esquerdo da boca (x, y),
    score

`FaceRecognizerSF.alignCrop` consome a mesma linha. O embedder reconstrói a
linha a partir do `DetectedFace`, então detector e embedder não precisam
detectar duas vezes.
"""

from collections.abc import Sequence

import numpy as np

from app.application.ports import BiometricProviderError
from app.domain.value_objects import BoundingBox, DetectedFace, FaceLandmarks, Point

YUNET_ROW_SIZE = 15


def face_from_yunet_row(row: Sequence[float]) -> DetectedFace:
    if len(row) != YUNET_ROW_SIZE:
        raise BiometricProviderError("linha do YuNet com formato inesperado")
    v = [float(value) for value in row]
    return DetectedFace(
        box=BoundingBox(x=v[0], y=v[1], width=v[2], height=v[3]),
        confidence=v[14],
        landmarks=FaceLandmarks(
            right_eye=Point(v[4], v[5]),
            left_eye=Point(v[6], v[7]),
            nose_tip=Point(v[8], v[9]),
            mouth_right=Point(v[10], v[11]),
            mouth_left=Point(v[12], v[13]),
        ),
    )


def yunet_row_from_face(face: DetectedFace) -> np.ndarray:
    if face.landmarks is None:
        raise BiometricProviderError("o alinhamento do SFace exige os 5 landmarks")
    lm = face.landmarks
    points = (lm.right_eye, lm.left_eye, lm.nose_tip, lm.mouth_right, lm.mouth_left)
    return np.array(
        [
            face.box.x,
            face.box.y,
            face.box.width,
            face.box.height,
            *(coordinate for p in points for coordinate in (p.x, p.y)),
            face.confidence if face.confidence is not None else 0.0,
        ],
        dtype=np.float32,
    )
