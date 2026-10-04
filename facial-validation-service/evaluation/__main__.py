"""Suíte de avaliação biométrica (Fase 4). Roda dentro do container `api`.

    python -m evaluation download-lfw            # LFW + pairs.txt, SHA-256 conferido
    python -m evaluation lfw                     # os 10 folds (View 2)
    python -m evaluation lfw --folds 1 --throughput 1

Avalia os componentes CONFIGURADOS (BIOMETRIC_* do ambiente), pelas portas do
serviço. Resultado em evaluation-results/<dataset>-<UTC>/ (fora do Git).
"""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.infrastructure.biometric import build_biometric_setup
from evaluation import lfw, report, runner

DEFAULT_DATASET_DIR = os.environ.get("EVALUATION_DATASET_DIR", "datasets/lfw")
DEFAULT_OUT = os.environ.get("EVALUATION_RESULTS_DIR", "evaluation-results")


def _folds(value: str) -> list[int]:
    if value == "all":
        return list(range(1, 11))
    folds = sorted({int(v) for v in value.split(",")})
    if not all(1 <= f <= 10 for f in folds):
        raise argparse.ArgumentTypeError("folds vão de 1 a 10")
    return folds


def _threads(value: str) -> tuple[int, ...]:
    return tuple(int(v) for v in value.split(",") if v)


async def evaluate_lfw(
    dataset_dir: Path,
    folds: list[int],
    throughput_threads: tuple[int, ...],
    throughput_sample: int,
    progress: bool = True,
) -> tuple[dict, list[tuple]]:
    settings = get_settings()
    setup = build_biometric_setup(settings)
    components = setup.pipeline.components
    missing = [
        port
        for port, name in components.describe().items()
        if name == "none" and port in ("detector", "embedder", "comparator")
    ]
    if missing:
        raise SystemExit(
            f"componentes não configurados: {', '.join(missing)} (ver BIOMETRIC_* no .env)"
        )

    pairs = lfw.load_pairs(dataset_dir, {f - 1 for f in folds})
    keys = sorted({ref for p in pairs for ref in (p.a, p.b)}, key=lambda r: r.relative_path)
    if progress:
        print(f"{len(pairs)} pares, {len(keys)} imagens, componentes {components.describe()}")

    await setup.start()
    try:
        images, wall = await runner.analyze_all(
            components,
            setup.pipeline.quality_gate,
            dataset_dir,
            keys,
            concurrency=settings.biometric_inference_threads,
            progress=progress,
        )
        scored = await runner.score_pairs(components, pairs, images)
        large = await runner.large_image_latency(components)
    finally:
        await setup.close()

    if progress and throughput_threads:
        print(f"vazão: {throughput_threads} thread(s), {min(throughput_sample, len(keys))} imagens")
    tput = await runner.throughput(
        settings, dataset_dir, keys[:throughput_sample], throughput_threads
    )

    result = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": {
            "name": "LFW View 2",
            "folds": folds,
            "pairs": len(pairs),
            "images": len(keys),
            "failure_to_acquire": sum(1 for r in images.values() if r.face_count == 0),
            "pairs_sha256": lfw.sha256_of(dataset_dir / lfw.LFW_PAIRS.filename),
        },
        "recognition": runner.recognition_metrics(scored),
        "service": runner.service_metrics(images),
        "latency": runner.latency_metrics(images, wall),
        "throughput": tput,
        "large_images": large,
        "environment": runner.environment(settings, components),
    }
    rows = [
        (
            p.fold + 1,
            int(p.genuine),
            p.a.relative_path,
            p.b.relative_path,
            "" if s is None else round(s, 6),
        )
        for p, s in scored
    ]
    return result, rows


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    dl = sub.add_parser("download-lfw")
    dl.add_argument("--dir", default=DEFAULT_DATASET_DIR)
    run = sub.add_parser("lfw")
    run.add_argument("--dir", default=DEFAULT_DATASET_DIR)
    run.add_argument("--folds", type=_folds, default=_folds("all"), help="all ou ex.: 1,2")
    run.add_argument("--throughput", type=_threads, default=(1, 2, 4), help="ex.: 1,2,4")
    run.add_argument("--throughput-sample", type=int, default=600)
    run.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()

    if args.command == "download-lfw":
        lfw.download(Path(args.dir))
        return

    dataset_dir = Path(args.dir)
    if not lfw.is_available(dataset_dir):
        sys.exit(f"LFW não encontrado em {dataset_dir} (python -m evaluation download-lfw)")
    result, rows = asyncio.run(
        evaluate_lfw(dataset_dir, args.folds, args.throughput, args.throughput_sample)
    )
    stamp = result["generated_at"].replace(":", "").replace("-", "").replace("+0000", "Z")
    out_dir = Path(args.out) / f"lfw-{stamp}"
    report.write(out_dir, result, rows)
    print(report.markdown(result))
    print(f"\nrelatório: {out_dir}/report.md (JSON e scores.csv ao lado)")


if __name__ == "__main__":
    main()
