"""Candidatos avaliados no POC, isolados do core do serviço.

- OpenCVEngine: YuNet (detecção + 5 landmarks) + SFace (embedding 128-d, cosseno).
- DlibEngine:   HOG frontal + shape predictor 5 pts + ResNet (embedding 128-d, euclidiana).
                São os mesmos pesos de reconhecimento usados pelo face-api.js.
- MiniFASNetLiveness: liveness passivo RGB (Silent-Face-Anti-Spoofing, 2 modelos).

Nada aqui é importado por app/.
"""

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"


@dataclass
class Face:
    box: tuple[int, int, int, int]  # x, y, w, h
    score: float
    raw: object  # dado nativo do motor (linha YuNet ou retângulo dlib)


class OpenCVEngine:
    name = "opencv-yunet-sface"
    metric = "cosine"  # maior = mais parecido
    model = "sface_2021dec+yunet_2023mar"

    def __init__(self, score_threshold: float = 0.6) -> None:
        self.detector = cv2.FaceDetectorYN.create(
            str(MODELS / "face_detection_yunet_2023mar.onnx"), "", (320, 320), score_threshold, 0.3, 5000
        )
        self.recognizer = cv2.FaceRecognizerSF.create(
            str(MODELS / "face_recognition_sface_2021dec.onnx"), ""
        )

    def detect(self, img: np.ndarray) -> list[Face]:
        h, w = img.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(img)
        if faces is None:
            return []
        return [
            Face(box=tuple(int(v) for v in f[:4]), score=float(f[14]), raw=f) for f in faces
        ]

    def embed(self, img: np.ndarray, face: Face) -> np.ndarray:
        aligned = self.recognizer.alignCrop(img, face.raw)
        return self.recognizer.feature(aligned).copy()

    def compare(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(self.recognizer.match(a, b, cv2.FaceRecognizerSF_FR_COSINE))


class DlibEngine:
    name = "dlib-resnet"
    metric = "euclidean"  # menor = mais parecido
    model = "dlib_face_recognition_resnet_model_v1+hog+sp5"

    def __init__(self) -> None:
        import dlib

        self._dlib = dlib
        self.detector = dlib.get_frontal_face_detector()
        self.shape = dlib.shape_predictor(str(MODELS / "shape_predictor_5_face_landmarks.dat"))
        self.rec = dlib.face_recognition_model_v1(
            str(MODELS / "dlib_face_recognition_resnet_model_v1.dat")
        )

    def detect(self, img: np.ndarray) -> list[Face]:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        rects, scores, _ = self.detector.run(rgb, 1, 0.0)
        return [
            Face(box=(r.left(), r.top(), r.width(), r.height()), score=float(s), raw=r)
            for r, s in zip(rects, scores, strict=True)
        ]

    def embed(self, img: np.ndarray, face: Face) -> np.ndarray:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        shape = self.shape(rgb, face.raw)
        return np.array(self.rec.compute_face_descriptor(rgb, shape), dtype=np.float32)

    def compare(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(np.linalg.norm(a - b))


def _load_minifasnet_module():
    spec = importlib.util.spec_from_file_location("MiniFASNet", ROOT / "vendor" / "MiniFASNet.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["MiniFASNet"] = module
    spec.loader.exec_module(module)
    return module


def _crop_box(src_w: int, src_h: int, box, scale: float):
    """Mesma lógica de CropImage._get_new_box do Silent-Face-Anti-Spoofing."""
    x, y, bw, bh = box
    scale = min((src_h - 1) / bh, min((src_w - 1) / bw, scale))
    nw, nh = bw * scale, bh * scale
    cx, cy = bw / 2 + x, bh / 2 + y
    ltx, lty, rbx, rby = cx - nw / 2, cy - nh / 2, cx + nw / 2, cy + nh / 2
    if ltx < 0:
        rbx -= ltx
        ltx = 0
    if lty < 0:
        rby -= lty
        lty = 0
    if rbx > src_w - 1:
        ltx -= rbx - src_w + 1
        rbx = src_w - 1
    if rby > src_h - 1:
        lty -= rby - src_h + 1
        rby = src_h - 1
    return int(ltx), int(lty), int(rbx), int(rby)


class MiniFASNetLiveness:
    """Classes: 0 = spoof(2D), 1 = real, 2 = spoof(3D). Soma dos dois modelos / 2."""

    name = "minifasnet-v2+v1se"

    def __init__(self) -> None:
        import torch

        self.torch = torch
        torch.set_num_threads(max(1, torch.get_num_threads()))
        m = _load_minifasnet_module()
        self.models = []
        for file, cls, scale in (
            ("2.7_80x80_MiniFASNetV2.pth", m.MiniFASNetV2, 2.7),
            ("4_0_0_80x80_MiniFASNetV1SE.pth", m.MiniFASNetV1SE, 4.0),
        ):
            net = cls(conv6_kernel=(5, 5))
            state = torch.load(MODELS / file, map_location="cpu", weights_only=True)
            state = {k.removeprefix("module."): v for k, v in state.items()}
            net.load_state_dict(state)
            net.eval()
            self.models.append((net, scale))

    def predict(self, img: np.ndarray, face: Face) -> dict:
        h, w = img.shape[:2]
        total = np.zeros(3)
        for net, scale in self.models:
            ltx, lty, rbx, rby = _crop_box(w, h, face.box, scale)
            crop = cv2.resize(img[lty : rby + 1, ltx : rbx + 1], (80, 80))
            tensor = self.torch.from_numpy(crop.transpose((2, 0, 1))).float().unsqueeze(0)
            with self.torch.no_grad():
                total += self.torch.softmax(net(tensor), dim=1).numpy()[0]
        probs = total / 2
        label = int(np.argmax(probs))
        return {"label": "REAL" if label == 1 else "SPOOF", "real_score": float(probs[1])}
