"""Cenários do item 7: mesma pessoa, pessoas diferentes, iluminação, câmera, imagem ruim,
rosto pequeno, oclusão, múltiplos rostos e sem rosto.

A decisão usa o threshold do EER no LFW apenas como REFERÊNCIA de POC
(não é calibração de produção). Resultado segue o pipeline do serviço:
0 rostos -> NO_FACE, >1 rosto -> MULTIPLE_FACES, 1 rosto -> MATCH/NO_MATCH.

Uso: python evaluate_scenarios.py opencv|dlib
"""

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from engines import DlibEngine, OpenCVEngine
from measure import measured

ROOT = Path(__file__).resolve().parents[1]


def quality_metrics(img, face):
    x, y, w, h = face.box
    roi = img[max(0, y) : y + h, max(0, x) : x + w]
    gray = cv2.cvtColor(roi if roi.size else img, cv2.COLOR_BGR2GRAY)
    return {
        "face_px": int(min(w, h)),
        "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1),
        "brightness": round(float(gray.mean()), 1),
    }


def largest(faces):
    return max(faces, key=lambda f: f.box[2] * f.box[3])


def main(which: str) -> None:
    engine = OpenCVEngine() if which == "opencv" else DlibEngine()
    higher = engine.metric == "cosine"
    thr = json.loads((ROOT / "results" / f"lfw_{which}.json").read_text())["eer"]["threshold"]
    manifest = json.loads((ROOT / "dataset" / "manifest.json").read_text())

    ref_cache = {}
    rows = []
    for case in manifest:
        ref_path = case["reference"]
        if ref_path not in ref_cache:
            ref = cv2.imread(str(ROOT / ref_path))
            faces = engine.detect(ref)
            # Referência: maior rosto (LFW tem rostos de fundo; em selfie de cadastro o
            # serviço exigiria exatamente 1 rosto).
            ref_cache[ref_path] = engine.embed(ref, largest(faces)) if faces else None
        ref_emb = ref_cache[ref_path]

        img = cv2.imread(str(ROOT / case["probe"]))
        m = {}
        with measured(m, "detect_"):
            faces = engine.detect(img)
        row = {"engine": engine.name, "case": case["case"], "scenario": case["scenario"],
               "input": case["probe"], "faces": len(faces), "score": None, "result": None}
        # result: política estrita do serviço (exatamente 1 rosto).
        # largest_result: comparação usando o maior rosto, para isolar o efeito do
        # cenário do efeito de rostos de fundo presentes no LFW.
        if not faces:
            row["result"] = row["largest_result"] = "NO_FACE"
        elif ref_emb is None:
            row["result"] = row["largest_result"] = "REFERENCE_UNUSABLE"
        else:
            face = largest(faces)
            with measured(m, "embed_"):
                emb = engine.embed(img, face)
            with measured(m, "compare_"):
                score = engine.compare(ref_emb, emb)
            row["score"] = round(score, 4)
            matched = score >= thr if higher else score <= thr
            row["largest_result"] = "MATCH" if matched else "NO_MATCH"
            row["result"] = "MULTIPLE_FACES" if len(faces) > 1 else row["largest_result"]
            row.update(quality_metrics(img, face))
        row.update(m)
        row["total_ms"] = round(sum(v for k, v in m.items() if k.endswith("_ms")), 2)
        rows.append(row)

    out = ROOT / "results"
    fields = ["engine", "case", "scenario", "input", "faces", "result", "largest_result", "score", "face_px",
              "sharpness", "brightness", "detect_ms", "embed_ms", "compare_ms", "total_ms",
              "detect_cpu_pct", "embed_cpu_pct", "detect_rss_mb", "embed_rss_mb"]
    with (out / f"scenarios_{which}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    summary = {}
    by = defaultdict(list)
    for r in rows:
        by[r["scenario"]].append(r)
    for scenario, items in by.items():
        results, largest_results = defaultdict(int), defaultdict(int)
        for r in items:
            results[r["result"]] += 1
            largest_results[r["largest_result"]] += 1
        scores = [r["score"] for r in items if r["score"] is not None]
        cpu = [r.get("embed_cpu_pct") or r.get("detect_cpu_pct") for r in items]
        summary[scenario] = {
            "n": len(items),
            "results_strict_single_face": dict(results),
            "results_largest_face": dict(largest_results),
            "score_mean": round(float(np.mean(scores)), 4) if scores else None,
            "score_min": round(float(np.min(scores)), 4) if scores else None,
            "score_max": round(float(np.max(scores)), 4) if scores else None,
            "total_ms_mean": round(float(np.mean([r["total_ms"] for r in items])), 2),
            "cpu_pct_mean": round(float(np.mean([c for c in cpu if c is not None])), 1),
            "rss_mb_max": max(r.get("embed_rss_mb") or r["detect_rss_mb"] for r in items),
        }
    doc = {"engine": engine.name, "threshold_reference_eer_lfw": thr, "metric": engine.metric,
           "scenarios": summary}
    (out / f"scenarios_{which}.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    print(json.dumps(doc, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
