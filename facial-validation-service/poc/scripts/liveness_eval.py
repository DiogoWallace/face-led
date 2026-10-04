"""POC de liveness passivo (MiniFASNet) em imagens e vídeo.

IMPORTANTE: o dataset disponível NÃO contém capturas ao vivo nem ataques físicos
(foto impressa, tela, replay). Os números aqui mostram o comportamento do modelo
em fotos digitais do LFW e num vídeo sintético feito de uma foto estática. Não
são evidência de proteção contra ataques.

Categorias exigidas pelo pedido e onde colocar capturas autorizadas:
  dataset/liveness/real/          pessoa real diante da câmera (com consentimento)
  dataset/liveness/print/         foto impressa apresentada à câmera
  dataset/liveness/screen/        foto exibida em tela
  dataset/liveness/video_replay/  vídeo reproduzido em tela
Uso: python liveness_eval.py
"""

import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from engines import MiniFASNetLiveness, OpenCVEngine
from measure import measured

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "dataset" / "liveness"
CATEGORIES = ("real", "print", "screen", "video_replay")


def predict_image(det, live, img):
    faces = det.detect(img)
    if len(faces) != 1:
        return {"label": "NO_SINGLE_FACE", "faces": len(faces)}
    m = {}
    with measured(m):
        out = live.predict(img, faces[0])
    return {**out, **m}


def predict_video(det, live, path, max_frames=30):
    cap = cv2.VideoCapture(str(path))
    frames = []
    while len(frames) < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    m = {}
    with measured(m):
        preds = [predict_image(det, live, f) for f in frames]
    scores = [p["real_score"] for p in preds if "real_score" in p]
    labels = Counter(p["label"] for p in preds)
    # Agregação do POC: maioria dos quadros; média do score de "real".
    return {"frames": len(frames), "labels": dict(labels),
            "real_score_mean": round(float(np.mean(scores)), 4) if scores else None,
            "result": labels.most_common(1)[0][0] if labels else "NO_FRAMES", **m}


def summarize(items):
    labels = Counter(i["label"] for i in items)
    scores = [i["real_score"] for i in items if "real_score" in i]
    ms = [i["ms"] for i in items if "ms" in i]
    return {"n": len(items), "labels": dict(labels),
            "real_score_mean": round(float(np.mean(scores)), 4) if scores else None,
            "ms_mean": round(float(np.mean(ms)), 2) if ms else None}


def main() -> None:
    det, live = OpenCVEngine(), MiniFASNetLiveness()
    manifest = json.loads((ROOT / "dataset" / "manifest.json").read_text())
    report = {"model": live.name, "warning": __doc__.split("\n\n")[1], "sets": {}}

    refs = sorted({c["reference"] for c in manifest})
    report["sets"]["lfw_digital_photos"] = summarize(
        [predict_image(det, live, cv2.imread(str(ROOT / r))) for r in refs])
    for scenario in ("lighting_dark", "lighting_bright", "camera_sim", "blur"):
        items = [c["probe"] for c in manifest if c["scenario"] == scenario]
        report["sets"][f"lfw_{scenario}"] = summarize(
            [predict_image(det, live, cv2.imread(str(ROOT / p))) for p in items])

    videos = sorted(LIVE.glob("*_static_photo_video.avi"))
    report["sets"]["synthetic_static_photo_video"] = [
        {"input": v.name, **predict_video(det, live, v)} for v in videos]

    for cat in CATEGORIES:
        files = sorted(f for f in (LIVE / cat).glob("*") if not f.name.startswith(".")) if (LIVE / cat).exists() else []
        if not files:
            report["sets"][cat] = "NOT TESTED - sem capturas autorizadas disponíveis"
            continue
        imgs = [f for f in files if f.suffix.lower() in (".jpg", ".jpeg", ".png")]
        vids = [f for f in files if f.suffix.lower() in (".mp4", ".avi", ".webm", ".mov")]
        report["sets"][cat] = {
            "images": summarize([predict_image(det, live, cv2.imread(str(f))) for f in imgs]),
            "videos": [{"input": v.name, **predict_video(det, live, v)} for v in vids],
        }

    (ROOT / "results" / "liveness.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
