"""Manifesto dos modelos ONNX do OpenCV e verificação de integridade.

Os arquivos ficam em `BIOMETRIC_MODELS_DIR` (padrão `models/`), fora do Git e
da imagem. O SHA-256 é conferido no download e de novo antes de carregar: um
arquivo trocado ou truncado impede o worker de subir.

Origem, licença e hashes conferidos no POC (poc/results/assets.json).
"""

import hashlib
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class ModelIntegrityError(Exception):
    """Modelo ausente ou com SHA-256 diferente do manifesto."""


@dataclass(frozen=True, slots=True)
class ModelFile:
    filename: str
    url: str
    sha256: str
    size_bytes: int
    license: str


YUNET = ModelFile(
    filename="face_detection_yunet_2023mar.onnx",
    url=(
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
        "face_detection_yunet_2023mar.onnx"
    ),
    sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    size_bytes=232_589,
    license="MIT (opencv_zoo)",
)

SFACE = ModelFile(
    filename="face_recognition_sface_2021dec.onnx",
    url=(
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/"
        "face_recognition_sface_2021dec.onnx"
    ),
    sha256="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    size_bytes=38_696_353,
    license="Apache-2.0 (opencv_zoo)",
)

ALL_MODELS = (YUNET, SFACE)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model(models_dir: Path, model: ModelFile) -> Path:
    path = models_dir / model.filename
    if not path.is_file():
        raise ModelIntegrityError(
            f"modelo ausente: {path} (baixe com: python -m app.cli download-models)"
        )
    if sha256_of(path) != model.sha256:
        raise ModelIntegrityError(f"SHA-256 divergente: {path}")
    return path


def download_model(models_dir: Path, model: ModelFile, *, timeout: float = 120) -> bool:
    """Baixa o modelo se ausente ou inválido. Devolve True se baixou.

    Grava num arquivo temporário e só renomeia depois de conferir tamanho e
    SHA-256 — um download interrompido nunca deixa um modelo "válido" pela metade.
    """
    target = models_dir / model.filename
    if target.is_file() and sha256_of(target) == model.sha256:
        return False
    if not model.url.startswith("https://"):
        raise ModelIntegrityError(f"URL de modelo não é https: {model.url}")

    models_dir.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(model.url, timeout=timeout) as response:  # noqa: S310
            with partial.open("wb") as file:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
                    file.write(chunk)
        if size != model.size_bytes or digest.hexdigest() != model.sha256:
            raise ModelIntegrityError(f"download de {model.filename} não confere com o manifesto")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return True
