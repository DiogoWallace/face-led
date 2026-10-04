"""LFW (Labeled Faces in the Wild), protocolo View 2: 10 folds × (300 genuínos + 300 impostores).

Mesma origem e mesmos SHA-256 do POC (poc/results/assets.json). Licença: sem
licença explícita, uso acadêmico/pesquisa (vis-www.cs.umass.edu/lfw).

O LFW são fotos de imprensa, 250×250, centradas no rosto de interesse. **Não
representa** selfies nem a demografia do público: serve para validar que o
motor funciona dentro do serviço, não para calibrar produção (ADR-003).
"""

import hashlib
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "facial-validation-service-evaluation"


@dataclass(frozen=True, slots=True)
class RemoteFile:
    filename: str
    url: str
    sha256: str
    size_bytes: int


LFW_ARCHIVE = RemoteFile(
    filename="lfw.tgz",
    url="https://ndownloader.figshare.com/files/5976018",
    sha256="055f7d9c632d7370e6fb4afc7468d40f970c34a80d4c6f50ffec63f5a8d536c0",
    size_bytes=180_566_744,
)
LFW_PAIRS = RemoteFile(
    filename="pairs.txt",
    url="https://ndownloader.figshare.com/files/5976006",
    sha256="ea42330c62c92989f9d7c03237ed5d591365e89b3e649747777b70e692dc1592",
    size_bytes=155_335,
)


@dataclass(frozen=True, slots=True)
class ImageRef:
    name: str
    number: int

    @property
    def relative_path(self) -> str:
        return f"lfw/{self.name}/{self.name}_{self.number:04d}.jpg"


@dataclass(frozen=True, slots=True)
class Pair:
    fold: int
    genuine: bool
    a: ImageRef
    b: ImageRef


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(remote: RemoteFile, directory: Path) -> bool:
    """Baixa e confere tamanho e SHA-256. Devolve True se baixou."""
    target = directory / remote.filename
    if target.is_file() and sha256_of(target) == remote.sha256:
        return False
    directory.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    digest, size = hashlib.sha256(), 0
    request = urllib.request.Request(remote.url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
            with partial.open("wb") as file:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
                    file.write(chunk)
        if size != remote.size_bytes or digest.hexdigest() != remote.sha256:
            raise RuntimeError(f"{remote.filename} não confere com o SHA-256 esperado")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return True


def download(directory: Path) -> None:
    for remote in (LFW_ARCHIVE, LFW_PAIRS):
        state = "baixado" if fetch(remote, directory) else "já presente"
        print(f"{remote.filename}: {state}, SHA-256 conferido")
    if not (directory / "lfw").is_dir():
        with tarfile.open(directory / LFW_ARCHIVE.filename) as archive:
            # filter="data" recusa caminhos absolutos, "..", links e arquivos especiais.
            archive.extractall(directory, filter="data")  # noqa: S202
        print("lfw.tgz: extraído")


def is_available(directory: Path) -> bool:
    return (directory / LFW_PAIRS.filename).is_file() and (directory / "lfw").is_dir()


def parse_pairs(text: str) -> list[Pair]:
    lines = text.splitlines()
    folds, per_fold = map(int, lines[0].split())
    pairs = []
    for index, line in enumerate(lines[1:]):
        parts = line.split("\t")
        fold = index // (2 * per_fold)
        if len(parts) == 3:
            name, n1, n2 = parts
            pairs.append(Pair(fold, True, ImageRef(name, int(n1)), ImageRef(name, int(n2))))
        elif len(parts) == 4:
            name1, n1, name2, n2 = parts
            pairs.append(Pair(fold, False, ImageRef(name1, int(n1)), ImageRef(name2, int(n2))))
        else:
            raise ValueError(f"linha {index + 2} de pairs.txt com formato inesperado")
    if len(pairs) != folds * 2 * per_fold:
        raise ValueError("pairs.txt com quantidade de pares diferente do cabeçalho")
    return pairs


def load_pairs(directory: Path, folds: set[int] | None = None) -> list[Pair]:
    pairs = parse_pairs((directory / LFW_PAIRS.filename).read_text())
    return [p for p in pairs if folds is None or p.fold in folds]
