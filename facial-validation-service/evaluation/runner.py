"""Avaliação dos componentes biométricos CONFIGURADOS, pelas portas do serviço.

Não usa o motor do POC: monta o mesmo `BiometricSetup` do worker
(`build_biometric_setup`) e chama detector, medidor de qualidade, embedder e
comparator pelas portas. O que é medido aqui é o que o serviço executa.

Duas camadas por imagem:
- reconhecimento: o MAIOR rosto detectado (o LFW é centrado no rosto de
  interesse) → embedding → similaridade dos pares → FAR/FRR/EER/10-fold;
- serviço: o que o `QualityGate` configurado faria com a mesma detecção
  (aceita, NO_FACE, MULTIPLE_FACES, LOW_QUALITY + motivos).
"""

import asyncio
import os
import platform
import resource
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.application.ports import (
    BiometricProviderError,
    BiometricProviderNotConfigured,
    FaceAnalysisComponents,
)
from app.config import Settings
from app.domain.services import QualityGate, QualityMeasurementUnavailable
from app.domain.value_objects import CaptureData, DetectedFace, FaceEmbedding
from app.infrastructure.biometric import build_biometric_setup
from evaluation import lfw, metrics

TARGET_FARS = (1e-2, 1e-3)
LARGE_IMAGE_SIZES = ((640, 480), (1280, 720), (1920, 1080), (4000, 3000))


@dataclass
class ImageResult:
    key: lfw.ImageRef
    face_count: int = 0
    embedding: FaceEmbedding | None = None
    service_outcome: str = "NOT_EVALUATED"
    quality_issues: tuple[str, ...] = ()
    timings_ms: dict[str, float] = field(default_factory=dict)
    error: str | None = None


def largest_face(faces: tuple[DetectedFace, ...]) -> DetectedFace | None:
    return max(faces, key=lambda f: f.box.area) if faces else None


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


async def analyze(
    components: FaceAnalysisComponents, gate: QualityGate, key: lfw.ImageRef, content: bytes
) -> ImageResult:
    result = ImageResult(key=key)
    capture = CaptureData(content=content, content_type="image/jpeg")
    try:
        t = time.perf_counter()
        detection = await components.detector.detect(capture)
        result.timings_ms["detect"] = _ms(t)
        result.face_count = detection.face_count

        face = largest_face(detection.faces)
        if face is None:
            result.service_outcome = "NO_FACE"
            return result

        rejection = gate.face_count_rejection(detection)
        considered = gate.considered_faces(detection)
        try:
            t = time.perf_counter()
            measurements = await components.quality_assessor.measure(
                capture, considered[0] if len(considered) == 1 else face
            )
            result.timings_ms["quality"] = _ms(t)
        except BiometricProviderNotConfigured:
            measurements = None
        if rejection is not None:
            reason, report = rejection
            result.service_outcome, result.quality_issues = reason.value, report.issues
        elif measurements is None:
            result.service_outcome = "QUALITY_ASSESSOR_NOT_CONFIGURED"
        else:
            try:
                report = gate.evaluate(detection, considered[0], measurements)
                result.service_outcome = "ACCEPTED" if report.passed else "LOW_QUALITY"
                result.quality_issues = report.issues
            except QualityMeasurementUnavailable:
                result.service_outcome = "QUALITY_MEASUREMENT_UNAVAILABLE"

        t = time.perf_counter()
        result.embedding = await components.embedder.embed(capture, face)
        result.timings_ms["embed"] = _ms(t)
    except BiometricProviderError as error:
        result.error = type(error).__name__
    return result


async def analyze_all(
    components: FaceAnalysisComponents,
    gate: QualityGate,
    dataset_dir: Path,
    keys: list[lfw.ImageRef],
    concurrency: int,
    progress: bool = False,
) -> tuple[dict[lfw.ImageRef, ImageResult], float]:
    # Concorrência = threads de inferência: cada imagem roda suas etapas sem fila,
    # então o tempo por etapa é latência, não espera no executor.
    semaphore = asyncio.Semaphore(concurrency)
    done = 0

    async def one(key: lfw.ImageRef) -> ImageResult:
        nonlocal done
        async with semaphore:
            content = (dataset_dir / key.relative_path).read_bytes()
            result = await analyze(components, gate, key, content)
        done += 1
        if progress and done % 500 == 0:
            print(f"  {done}/{len(keys)} imagens", flush=True)
        return result

    start = time.perf_counter()
    results = await asyncio.gather(*(one(k) for k in keys))
    return {r.key: r for r in results}, time.perf_counter() - start


async def score_pairs(
    components: FaceAnalysisComponents,
    pairs: list[lfw.Pair],
    images: dict[lfw.ImageRef, ImageResult],
) -> list[tuple[lfw.Pair, float | None]]:
    scored = []
    for pair in pairs:
        a, b = images[pair.a].embedding, images[pair.b].embedding
        score = None
        if a is not None and b is not None:
            score = await components.comparator.similarity(a, b)
        scored.append((pair, score))
    return scored


def recognition_metrics(scored: list[tuple[lfw.Pair, float | None]]) -> dict:
    valid = [(p, s) for p, s in scored if s is not None]
    folds = np.array([p.fold for p, _ in valid])
    genuine_mask = np.array([p.genuine for p, _ in valid])
    scores = np.array([s for _, s in valid])
    genuine, impostor = scores[genuine_mask], scores[~genuine_mask]

    eer = metrics.equal_error_rate(genuine, impostor)
    operating = {}
    for target in TARGET_FARS:
        point = metrics.threshold_at_far(genuine, impostor, target)
        operating[str(target)] = None if point is None else _point(point)
    mean, std, per_fold = (
        metrics.ten_fold_accuracy(folds, genuine_mask, scores)
        if len(np.unique(folds)) > 1
        else (None, None, [])
    )
    return {
        "pairs_evaluated": {"genuine": int(genuine.size), "impostor": int(impostor.size)},
        "pairs_skipped": len(scored) - len(valid),
        "min_measurable_far": round(1 / impostor.size, 6),
        "genuine_score": metrics.summarize(genuine),
        "impostor_score": metrics.summarize(impostor),
        "genuine_histogram": metrics.histogram(genuine),
        "impostor_histogram": metrics.histogram(impostor),
        "eer": _point(eer),
        "operating_points": operating,
        "ten_fold_accuracy": None
        if mean is None
        else {"mean": mean, "std": std, "per_fold": per_fold},
    }


def _point(point: metrics.OperatingPoint) -> dict[str, float]:
    return {"threshold": point.threshold, "far": point.far, "frr": point.frr}


def service_metrics(images: dict[lfw.ImageRef, ImageResult]) -> dict:
    outcomes = Counter(r.service_outcome for r in images.values())
    issues = Counter(i for r in images.values() for i in r.quality_issues)
    faces = Counter(min(r.face_count, 3) for r in images.values())
    return {
        "outcomes": dict(outcomes.most_common()),
        "quality_issues": dict(issues.most_common()),
        "faces_detected": {("3+" if k == 3 else str(k)): v for k, v in sorted(faces.items())},
        "errors": dict(Counter(r.error for r in images.values() if r.error).most_common()),
    }


def latency_metrics(images: dict[lfw.ImageRef, ImageResult], wall_seconds: float) -> dict:
    stages = {}
    for stage in ("detect", "quality", "embed"):
        values = [r.timings_ms[stage] for r in images.values() if stage in r.timings_ms]
        if values:
            summary = metrics.summarize(values)
            stages[stage] = {k: round(summary[k], 2) for k in ("mean", "p50", "p95", "p99", "max")}
    return {
        "per_stage_ms": stages,
        "images": len(images),
        "wall_seconds": round(wall_seconds, 1),
        "images_per_second": round(len(images) / wall_seconds, 2),
    }


async def throughput(
    settings: Settings,
    dataset_dir: Path,
    keys: list[lfw.ImageRef],
    thread_counts: tuple[int, ...],
) -> dict[str, dict]:
    """Vazão do pipeline no runtime do worker (sem fila e sem banco), por nº de threads."""
    out = {}
    for threads in thread_counts:
        setup = build_biometric_setup(
            settings.model_copy(update={"biometric_inference_threads": threads})
        )
        await setup.start()
        try:
            gate = setup.pipeline.quality_gate
            components = setup.pipeline.components
            # Concorrência maior que as threads: o executor fica sempre ocupado, como sob carga.
            _, wall = await analyze_all(components, gate, dataset_dir, keys, threads * 4)
        finally:
            await setup.close()
        out[str(threads)] = {
            "images": len(keys),
            "wall_seconds": round(wall, 2),
            "images_per_second": round(len(keys) / wall, 2),
        }
    # Memória por thread não é medida aqui: depois da passada principal o RSS do
    # processo não volta a cair, e o delta sai enganoso. Medir em processo limpo.
    return out


async def large_image_latency(components: FaceAnalysisComponents, repeats: int = 3) -> dict:
    """Detecção em imagens grandes sintéticas (sem rosto). O LFW só tem 250×250."""
    out = {}
    rng = np.random.default_rng(1)
    for width, height in LARGE_IMAGE_SIZES:
        image = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
        image = cv2.GaussianBlur(image, (0, 0), 3)  # JPEG de tamanho realista
        content = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
        capture = CaptureData(content=content, content_type="image/jpeg")
        await components.detector.detect(capture)  # aquecimento
        times = []
        for _ in range(repeats):
            t = time.perf_counter()
            await components.detector.detect(capture)
            times.append(_ms(t))
        out[f"{width}x{height}"] = {
            "jpeg_mb": round(len(content) / 1e6, 2),
            "detect_ms_mean": round(float(np.mean(times)), 1),
        }
    return out


def environment(settings: Settings, components: FaceAnalysisComponents) -> dict:
    return {
        "components": components.describe(),
        "inference_threads": settings.biometric_inference_threads,
        "opencv_threads": settings.biometric_opencv_threads or cv2.getNumThreads(),
        "detector_score_threshold": settings.biometric_detector_score_threshold,
        "quality_requirements": {
            "min_face_px": settings.quality_min_face_px,
            "min_face_ratio": settings.quality_min_face_ratio,
            "min_sharpness": settings.quality_min_sharpness,
            "min_brightness": settings.quality_min_brightness,
            "max_brightness": settings.quality_max_brightness,
        },
        "versions": {
            "python": platform.python_version(),
            "opencv": cv2.__version__,
            "numpy": np.__version__,
        },
        "cpu_count": os.cpu_count(),
        "cpu": _cpu_model(),
        "max_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "git_commit": _git_commit(),
    }


def _cpu_model() -> str | None:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def _git_commit() -> str | None:
    """Lê o commit do .git montado no container (a imagem não tem o binário git)."""
    git = Path(".git")
    try:
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head
        ref = head[5:]
        ref_file = git / ref
        if ref_file.is_file():
            return ref_file.read_text().strip()
        for line in (git / "packed-refs").read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split()[0]
    except OSError:
        pass
    return None
