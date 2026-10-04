"""Verifica se uma instância compartilhada do YuNet dá resultados consistentes entre threads."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
from engines import OpenCVEngine

ROOT = Path(__file__).resolve().parents[1]
engine = OpenCVEngine()
a = cv2.imread(str(ROOT / "dataset" / "same_person" / "000_ref.jpg"))
b = cv2.resize(a, (640, 480))
expected = {0: len(engine.detect(a)), 1: len(engine.detect(b))}
jobs = [(i % 2, a if i % 2 == 0 else b) for i in range(400)]
with ThreadPoolExecutor(4) as pool:
    got = list(pool.map(lambda j: (j[0], len(engine.detect(j[1]))), jobs))
bad = sum(1 for k, n in got if n != expected[k])
print(f"instância compartilhada, 4 threads, 2 tamanhos de imagem: {bad}/{len(got)} resultados divergentes")
