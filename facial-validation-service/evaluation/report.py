"""Relatório da avaliação: report.json (completo), report.md (legível) e scores.csv.

Os arquivos vão para `evaluation-results/` (fora do Git). O scores.csv tem os
nomes das imagens do dataset: com um dataset próprio, são dados pessoais.
"""

import csv
import json
from pathlib import Path

POC_BASELINE = Path("poc/results/lfw_opencv.json")
SFACE = "opencv-sface/2021dec"


def _pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value * 100:.{digits}f}%"


def _thr(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def poc_baseline(report: dict) -> dict | None:
    """Números do POC para o mesmo motor, para comparar (só faz sentido com o SFace)."""
    if report["environment"]["components"].get("embedder") != SFACE:
        return None
    if not POC_BASELINE.is_file() or report["dataset"]["folds"] != list(range(1, 11)):
        return None
    return json.loads(POC_BASELINE.read_text())


def write(out_dir: Path, report: dict, scored_rows: list[tuple]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    with (out_dir / "scores.csv").open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["fold", "genuine", "image_a", "image_b", "score"])
        writer.writerows(scored_rows)
    (out_dir / "report.md").write_text(markdown(report))


def markdown(report: dict) -> str:
    rec, svc, env = report["recognition"], report["service"], report["environment"]
    lat, ds = report["latency"], report["dataset"]
    baseline = poc_baseline(report)
    g, i = rec["genuine_score"], rec["impostor_score"]

    def poc(path: list[str], fmt=_pct) -> str:
        if baseline is None:
            return ""
        value = baseline
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        return f" | {fmt(value)}" if value is not None else " | —"

    head = "| Métrica | Serviço" + (" | POC |" if baseline else " |")
    sep = "|---|---" + ("|---|" if baseline else "|")
    eer, ops, tf = rec["eer"], rec["operating_points"], rec["ten_fold_accuracy"]
    lines = [
        f"# Avaliação — {ds['name']}",
        "",
        f"- **Gerado em:** {report['generated_at']} · commit `{env['git_commit'] or '?'}`",
        f"- **Componentes:** `{env['components']}`",
        f"- **Máquina:** {env['cpu']} ({env['cpu_count']} CPUs), "
        f"{env['inference_threads']} thread(s) de inferência, OpenCV {env['versions']['opencv']}",
        f"- **Dataset:** {ds['name']}, folds {ds['folds']}, {ds['images']} imagens, "
        f"{ds['pairs']} pares",
        "",
        "> Estes números descrevem este dataset nesta máquina. **Não são calibração de "
        "produção**: thresholds só entram na FaceMatchPolicy por configuração, depois de "
        "avaliação com capturas reais do público (ADR-003, plano §8).",
        "",
        "## 1. Reconhecimento (maior rosto de cada imagem)",
        "",
        head,
        sep,
        f"| Falha de detecção (imagens sem rosto) | {ds['failure_to_acquire']}"
        + (f" | {baseline['failure_to_acquire']} |" if baseline else " |"),
        f"| Pares avaliados (G / I) | {rec['pairs_evaluated']['genuine']} / "
        f"{rec['pairs_evaluated']['impostor']}"
        + (
            f" | {baseline['pairs_evaluated']['genuine']} / "
            f"{baseline['pairs_evaluated']['impostor']} |"
            if baseline
            else " |"
        ),
        f"| Score genuíno (média / p5) | {g['mean']:.3f} / {g['p5']:.3f}"
        + (
            f" | {baseline['genuine_score']['mean']:.3f} / {baseline['genuine_score']['p5']:.3f} |"
            if baseline
            else " |"
        ),
        f"| Score impostor (média / p95) | {i['mean']:.3f} / {i['p95']:.3f}"
        + (
            f" | {baseline['impostor_score']['mean']:.3f} / "
            f"{baseline['impostor_score']['p95']:.3f} |"
            if baseline
            else " |"
        ),
        f"| **EER** | **{_pct(eer['far'])}** (thr {_thr(eer['threshold'])})"
        + poc(["eer", "far"])
        + (" |" if baseline else " |"),
    ]
    for target, label in (("0.01", "FAR ≤ 1%"), ("0.001", "FAR ≤ 0,1%")):
        point = ops.get(target)
        cell = (
            "—"
            if point is None
            else f"thr {_thr(point['threshold'])} → FRR {_pct(point['frr'])} "
            f"(FAR {_pct(point['far'], 3)})"
        )
        poc_cell = ""
        if baseline:
            bp = baseline["operating_points"].get(target)
            poc_cell = (
                " | —" if bp is None else f" | thr {_thr(bp['threshold'])} → FRR {_pct(bp['frr'])}"
            )
        lines.append(f"| {label} | {cell}{poc_cell} |")
    if tf:
        lines.append(
            f"| Acurácia 10-fold | {_pct(tf['mean'])} ± {_pct(tf['std'])}"
            + (
                f" | {_pct(baseline['ten_fold_accuracy']['mean'])} ± "
                f"{_pct(baseline['ten_fold_accuracy']['std'])} |"
                if baseline
                else " |"
            )
        )
    lines += [
        "",
        f"Menor FAR mensurável com {rec['pairs_evaluated']['impostor']} impostores: "
        f"{_pct(rec['min_measurable_far'], 3)}. Pontos abaixo disso não têm significância.",
        "",
        "## 2. Serviço (QualityGate configurado, mesma detecção)",
        "",
        f"Requisitos em vigor: `{env['quality_requirements']}` "
        "(`None` = não aplicado, PENDING CALIBRATION).",
        "",
        "| Resultado | Imagens |",
        "|---|---|",
        *(f"| `{k}` | {v} |" for k, v in svc["outcomes"].items()),
        "",
        "Motivos de qualidade: "
        + (", ".join(f"`{k}` {v}" for k, v in svc["quality_issues"].items()) or "nenhum"),
        "",
        "Rostos detectados por imagem: "
        + ", ".join(f"{k}: {v}" for k, v in svc["faces_detected"].items()),
        "",
        "## 3. Latência e vazão",
        "",
        f"Passada principal: {lat['images']} imagens em {lat['wall_seconds']} s "
        f"({lat['images_per_second']} img/s), concorrência = threads de inferência.",
        "",
        "| Etapa | média | p50 | p95 | p99 | máx (ms) |",
        "|---|---|---|---|---|---|",
        *(
            f"| {stage} | {v['mean']} | {v['p50']} | {v['p95']} | {v['p99']} | {v['max']} |"
            for stage, v in lat["per_stage_ms"].items()
        ),
        "",
        "Vazão do pipeline no runtime do worker (detecção + qualidade + embedding; "
        "sem fila e sem banco):",
        "",
        "| Threads | Imagens | Segundos | img/s |",
        "|---|---|---|---|",
        *(
            f"| {t} | {v['images']} | {v['wall_seconds']} | {v['images_per_second']} |"
            for t, v in report["throughput"].items()
        ),
        "",
        "Detecção em imagem grande (sintética, sem rosto; o LFW só tem 250×250):",
        "",
        "| Resolução | JPEG (MB) | Detecção (ms) |",
        "|---|---|---|",
        *(
            f"| {size} | {v['jpeg_mb']} | {v['detect_ms_mean']} |"
            for size, v in report["large_images"].items()
        ),
        "",
        f"Memória máxima do processo de avaliação: {env['max_rss_mb']} MB — inclui os "
        "resultados de todas as imagens e os runtimes da medição de vazão; **não** é a "
        "memória do worker.",
        "",
    ]
    return "\n".join(lines)
