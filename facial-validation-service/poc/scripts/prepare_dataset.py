"""Monta os cenários de teste a partir do LFW (público) e de imagens sintéticas.

Nenhuma imagem pessoal/privada é usada. Variações de iluminação, câmera, blur,
tamanho, oclusão e múltiplos rostos são DERIVADAS por transformação de imagens
do LFW e estão rotuladas como simulação no manifesto.
"""

import json
import random
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
LFW = ROOT / "dataset" / "_raw" / "lfw"
DS = ROOT / "dataset"
N_CASES = 50
SEED = 20260930


def gamma(img, g):
    table = ((np.arange(256) / 255.0) ** g * 255).astype("uint8")
    return cv2.LUT(img, table)


def camera_sim(img, rng):
    # Simulação grosseira de outra câmera: resolução menor, JPEG forte, cor e ruído.
    small = cv2.resize(img, None, fx=0.45, fy=0.45, interpolation=cv2.INTER_AREA)
    up = cv2.resize(small, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
    ok, enc = cv2.imencode(".jpg", up, [cv2.IMWRITE_JPEG_QUALITY, 25])
    out = cv2.imdecode(enc, cv2.IMREAD_COLOR).astype(np.float32)
    out *= np.array([1.08, 0.97, 0.9], dtype=np.float32)  # dominante de cor (BGR)
    out += rng.normal(0, 6, out.shape)
    return np.clip(out, 0, 255).astype("uint8")


def small_face(img):
    face = cv2.resize(img, (40, 40), interpolation=cv2.INTER_AREA)  # rosto ~16-20 px
    canvas = np.full((480, 640, 3), 128, dtype="uint8")
    canvas[220:260, 300:340] = face
    return canvas


def occlusion(img, part):
    out = img.copy()
    if part == "lower":  # simula máscara cobrindo nariz/boca
        out[140:215, 75:175] = (40, 40, 40)
    else:  # simula óculos escuros
        out[95:130, 70:180] = (15, 15, 15)
    return out


def no_face(rng):
    base = rng.integers(0, 255, (250, 250, 3), dtype=np.uint8)
    return cv2.GaussianBlur(base, (0, 0), 6)


def main() -> None:
    rng = np.random.default_rng(SEED)
    random.seed(SEED)
    people = sorted(p for p in LFW.iterdir() if p.is_dir())
    multi = [p for p in people if len(list(p.glob("*.jpg"))) >= 2]
    single = [p for p in people if p not in multi]
    chosen = random.sample(multi, N_CASES)
    others = random.sample(single, N_CASES)

    for sub in ("same_person", "different_person", "quality", "liveness"):
        (DS / sub).mkdir(parents=True, exist_ok=True)

    manifest = []
    for i, (person, other) in enumerate(zip(chosen, others, strict=True)):
        a_path, b_path = sorted(person.glob("*.jpg"))[:2]
        o_path = sorted(other.glob("*.jpg"))[0]
        a, b, o = (cv2.imread(str(p)) for p in (a_path, b_path, o_path))
        ref = DS / "same_person" / f"{i:03d}_ref.jpg"
        cv2.imwrite(str(ref), a)
        cases = {
            "same_person": ("same_person", b, True, "outra foto LFW da mesma pessoa"),
            "different_person": ("different_person", o, False, "foto LFW de outra pessoa"),
            "lighting_dark": ("quality", gamma(b, 2.2), True, "SIMULAÇÃO: gamma 2.2"),
            "lighting_bright": ("quality", gamma(b, 0.45), True, "SIMULAÇÃO: gamma 0.45"),
            "camera_sim": ("quality", camera_sim(b, rng), True, "SIMULAÇÃO: resolução/JPEG/cor/ruído"),
            "blur": ("quality", cv2.GaussianBlur(b, (0, 0), 4), True, "SIMULAÇÃO: blur sigma 4"),
            "small_face": ("quality", small_face(b), True, "SIMULAÇÃO: rosto ~20 px em 640x480"),
            "occlusion_lower": ("quality", occlusion(b, "lower"), True, "SIMULAÇÃO: oclusão boca/nariz"),
            "occlusion_eyes": ("quality", occlusion(b, "eyes"), True, "SIMULAÇÃO: oclusão olhos"),
            "multiple_faces": ("quality", np.hstack([b, o]), True, "SIMULAÇÃO: 2 rostos lado a lado"),
            "no_face": ("quality", no_face(rng), None, "SINTÉTICO: ruído desfocado"),
        }
        for kind, (folder, img, genuine, note) in cases.items():
            path = DS / folder / f"{i:03d}_{kind}.jpg"
            cv2.imwrite(str(path), img)
            manifest.append({
                "case": i, "scenario": kind, "reference": str(ref.relative_to(ROOT)),
                "probe": str(path.relative_to(ROOT)), "genuine": genuine, "note": note,
                "source": [a_path.name, b_path.name] + ([o_path.name] if kind in ("different_person", "multiple_faces") else []),
            })

    # Vídeo sintético feito de UMA foto estática (equivale a replay/injeção digital de foto).
    for i in range(10):
        img = cv2.imread(str(DS / "same_person" / f"{i:03d}_ref.jpg"))
        path = DS / "liveness" / f"{i:03d}_static_photo_video.avi"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 15, (250, 250))
        for t in range(30):
            m = np.float32([[1, 0, 2 * np.sin(t / 4)], [0, 1, np.cos(t / 5)]])
            writer.write(cv2.warpAffine(img, m, (250, 250), borderMode=cv2.BORDER_REFLECT))
        writer.release()

    (DS / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    print(f"{len(manifest)} casos, 10 vídeos sintéticos")


if __name__ == "__main__":
    main()
