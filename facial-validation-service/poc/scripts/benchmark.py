"""Benchmark por etapa (1, 10 e 100 execuções) e concorrência (1, 2, 4 threads).

Mede: detecção, embedding, comparação, liveness; memória (RSS) e CPU do processo.
Uso: python benchmark.py
"""

import json
import os
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import psutil
from engines import DlibEngine, MiniFASNetLiveness, OpenCVEngine
from measure import rss_mb

ROOT = Path(__file__).resolve().parents[1]
PROC = psutil.Process(os.getpid())


def run(fn, n):
    cpu0 = PROC.cpu_times()
    times = []
    t0 = time.perf_counter()
    for _ in range(n):
        s = time.perf_counter()
        fn()
        times.append((time.perf_counter() - s) * 1000)
    wall = time.perf_counter() - t0
    cpu1 = PROC.cpu_times()
    cpu = (cpu1.user - cpu0.user) + (cpu1.system - cpu0.system)
    times.sort()
    return {
        "runs": n,
        "mean_ms": round(statistics.fmean(times), 3),
        "p50_ms": round(times[len(times) // 2], 3),
        "p95_ms": round(times[min(len(times) - 1, int(len(times) * 0.95))], 3),
        "max_ms": round(times[-1], 3),
        "cpu_pct": round(100 * cpu / wall, 1),
        "rss_mb": rss_mb(),
    }


def concurrency(factory, img, workers, total=40):
    # Uma instância por thread: FaceDetectorYN compartilhado entre threads devolveu
    # resultados vazios (estado interno de setInputSize/detect não é thread-safe).
    local = threading.local()

    def task(_):
        if not hasattr(local, "engine"):
            local.engine = factory()
        e = local.engine
        faces = e.detect(img)
        if not faces:
            return False
        face = max(faces, key=lambda f: f.box[2] * f.box[3])
        return e.embed(img, face) is not None

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(lambda _: local.__dict__.setdefault("engine", factory()), range(workers)))
        t0 = time.perf_counter()
        ok = sum(pool.map(task, range(total)))
        wall = time.perf_counter() - t0
    return {"workers": workers, "tasks": total, "ok": ok,
            "throughput_per_s": round(total / wall, 2), "rss_mb": rss_mb()}


def main() -> None:
    img = cv2.imread(str(ROOT / "dataset" / "same_person" / "000_ref.jpg"))
    selfie_like = cv2.resize(img, (640, 640))  # resolução típica de selfie reduzida
    report = {"cpu": os.cpu_count(), "cv2_threads": cv2.getNumThreads(), "rss_mb_start": rss_mb(),
              "input_sizes": {"lfw": list(img.shape[:2]), "upscaled": [640, 640]}, "engines": {}}

    for name, factory in (("opencv", OpenCVEngine), ("dlib", DlibEngine)):
        before = rss_mb()
        t0 = time.perf_counter()
        engine = factory()
        load_ms = round((time.perf_counter() - t0) * 1000, 1)
        face = engine.detect(img)[0]
        emb = engine.embed(img, face)
        emb2 = engine.embed(img, face)
        r = {"load_ms": load_ms, "rss_after_load_mb": rss_mb(), "rss_delta_load_mb": round(rss_mb() - before, 1)}
        for label, fn in (
            ("detect_250px", lambda e=engine: e.detect(img)),
            ("detect_640px", lambda e=engine: e.detect(selfie_like)),
            ("embed", lambda e=engine, f=face: e.embed(img, f)),
            ("compare", lambda e=engine: e.compare(emb, emb2)),
            ("pipeline_250px", lambda e=engine: e.embed(img, e.detect(img)[0])),
        ):
            r[label] = {str(n): run(fn, n) for n in (1, 10, 100)}
        r["concurrency_pipeline_250px"] = [concurrency(factory, img, w) for w in (1, 2, 4)]
        report["engines"][name] = r

    before = rss_mb()
    t0 = time.perf_counter()
    live = MiniFASNetLiveness()
    load_ms = round((time.perf_counter() - t0) * 1000, 1)
    face = OpenCVEngine().detect(img)[0]
    report["engines"]["minifasnet"] = {
        "load_ms": load_ms,
        "rss_delta_load_mb": round(rss_mb() - before, 1),
        "torch_threads": live.torch.get_num_threads(),
        "liveness": {str(n): run(lambda: live.predict(img, face), n) for n in (1, 10, 100)},
    }
    report["rss_mb_end"] = rss_mb()
    (ROOT / "results" / "benchmark.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
