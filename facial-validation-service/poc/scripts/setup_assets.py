"""Baixa modelos e o dataset público LFW para poc/models e poc/dataset/_raw.

Registra origem, licença e SHA-256 de cada arquivo em results/assets.json.
Nada aqui é versionado no Git (ver .gitignore).
"""

import bz2
import hashlib
import json
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
RAW = ROOT / "dataset" / "_raw"
VENDOR = ROOT / "vendor"

SF = "https://github.com/minivision-ai/Silent-Face-Anti-Spoofing/raw/master"
ASSETS = [
    # (destino, url, licença, finalidade)
    (MODELS / "face_detection_yunet_2023mar.onnx",
     "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
     "MIT (opencv_zoo)", "detecção + 5 landmarks"),
    (MODELS / "face_recognition_sface_2021dec.onnx",
     "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
     "Apache-2.0 (opencv_zoo)", "embedding SFace"),
    (MODELS / "shape_predictor_5_face_landmarks.dat",
     "http://dlib.net/files/shape_predictor_5_face_landmarks.dat.bz2",
     "Domínio público (dlib-models)", "alinhamento dlib"),
    (MODELS / "dlib_face_recognition_resnet_model_v1.dat",
     "http://dlib.net/files/dlib_face_recognition_resnet_model_v1.dat.bz2",
     "Domínio público (dlib-models)", "embedding dlib (mesmos pesos do face-api.js)"),
    (MODELS / "2.7_80x80_MiniFASNetV2.pth",
     f"{SF}/resources/anti_spoof_models/2.7_80x80_MiniFASNetV2.pth",
     "Apache-2.0 (Silent-Face-Anti-Spoofing)", "liveness passivo"),
    (MODELS / "4_0_0_80x80_MiniFASNetV1SE.pth",
     f"{SF}/resources/anti_spoof_models/4_0_0_80x80_MiniFASNetV1SE.pth",
     "Apache-2.0 (Silent-Face-Anti-Spoofing)", "liveness passivo"),
    (VENDOR / "MiniFASNet.py", f"{SF}/src/model_lib/MiniFASNet.py",
     "Apache-2.0 (Silent-Face-Anti-Spoofing)", "definição da rede"),
    (VENDOR / "LICENSE-Silent-Face-Anti-Spoofing", f"{SF}/LICENSE", "Apache-2.0", "licença"),
    # LFW via mirror do figshare usado pelo scikit-learn (fetch_lfw_people).
    (RAW / "lfw.tgz", "https://ndownloader.figshare.com/files/5976018",
     "Sem licença explícita; uso acadêmico/pesquisa (vis-www.cs.umass.edu/lfw)", "dataset"),
    (RAW / "pairs.txt", "https://ndownloader.figshare.com/files/5976006",
     "idem LFW", "pares oficiais (View 2)"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(dest: Path, url: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"baixando {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "facial-validation-poc"})
    with urllib.request.urlopen(req, timeout=300) as resp, tmp.open("wb") as out:  # noqa: S310
        data = resp.read()
        out.write(bz2.decompress(data) if url.endswith(".bz2") else data)
    tmp.rename(dest)


def main() -> None:
    record = []
    for dest, url, license_, purpose in ASSETS:
        fetch(dest, url)
        record.append({
            "file": str(dest.relative_to(ROOT)), "url": url, "license": license_,
            "purpose": purpose, "bytes": dest.stat().st_size, "sha256": sha256(dest),
        })
    lfw_dir = RAW / "lfw"
    if not lfw_dir.exists():
        with tarfile.open(RAW / "lfw.tgz") as tar:
            tar.extractall(RAW, filter="data")
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "assets.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
    print(json.dumps([{k: r[k] for k in ("file", "bytes")} for r in record], indent=1))


if __name__ == "__main__":
    main()
