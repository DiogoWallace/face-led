"""FaceDetector com OpenCV YuNet 2023mar (rostos + 5 landmarks)."""

from app.domain.value_objects import CaptureData, FaceDetectionResult
from app.infrastructure.biometric.opencv.models import YUNET
from app.infrastructure.biometric.opencv.runtime import OpenCVRuntime, decode_image
from app.infrastructure.biometric.opencv.yunet_format import face_from_yunet_row


class YuNetFaceDetector:
    """Só encontra rostos. Quantos são aceitáveis é decisão do QualityGate."""

    name = "opencv-yunet/2023mar"

    def __init__(self, runtime: OpenCVRuntime) -> None:
        runtime.require(YUNET)
        self._runtime = runtime

    async def detect(self, capture: CaptureData) -> FaceDetectionResult:
        return await self._runtime.run(self._detect, capture.content)

    def _detect(self, content: bytes) -> FaceDetectionResult:
        image = decode_image(content)
        height, width = image.shape[:2]
        detector = self._runtime.thread_detector()
        detector.setInputSize((width, height))
        _, rows = detector.detect(image)
        faces = () if rows is None else tuple(face_from_yunet_row(row) for row in rows)
        return FaceDetectionResult(faces=faces, image_width=width, image_height=height)
