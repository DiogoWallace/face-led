"""Verificação 1:1 no protocolo LFW View 2 (pairs.txt: 10 folds x 300 genuínos + 300 impostores).

Uso: python evaluate_lfw.py opencv|dlib
Saída: results/lfw_<engine>.json e results/lfw_<engine>_scores.csv
"""

import csv
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from engines import DlibEngine, OpenCVEngine
from measure import rss_mb

ROOT = Path(__file__).resolve().parents[1]
LFW = ROOT / "dataset" / "_raw" / "lfw"


def read_pairs():
    lines = (ROOT / "dataset" / "_raw" / "pairs.txt").read_text().splitlines()
    folds, per = map(int, lines[0].split())
    pairs = []
    for idx, line in enumerate(lines[1:]):
        parts = line.split("\t")
        fold = idx // (2 * per)
        if len(parts) == 3:
            pairs.append((fold, True, (parts[0], int(parts[1])), (parts[0], int(parts[2]))))
        else:
            pairs.append((fold, False, (parts[0], int(parts[1])), (parts[2], int(parts[3]))))
    return pairs


def path_of(name, n):
    return LFW / name / f"{name}_{n:04d}.jpg"


def largest_face(faces):
    return max(faces, key=lambda f: f.box[2] * f.box[3]) if faces else None


def rates(genuine, impostor, thr, higher_better):
    if higher_better:
        far = float(np.mean(impostor >= thr))
        frr = float(np.mean(genuine < thr))
    else:
        far = float(np.mean(impostor <= thr))
        frr = float(np.mean(genuine > thr))
    return far, frr


def main(which: str) -> None:
    engine = OpenCVEngine() if which == "opencv" else DlibEngine()
    higher = engine.metric == "cosine"
    pairs = read_pairs()
    needed = sorted({p for _, _, a, b in pairs for p in (a, b)})

    embeddings, fta, t_det, t_emb = {}, [], [], []
    t0 = time.perf_counter()
    for key in needed:
        img = cv2.imread(str(path_of(*key)))
        s = time.perf_counter()
        # LFW é centrado no rosto de interesse; outros rostos podem aparecer ao fundo.
        face = largest_face(engine.detect(img))
        t_det.append(time.perf_counter() - s)
        if face is None:
            fta.append(key)
            continue
        s = time.perf_counter()
        embeddings[key] = engine.embed(img, face)
        t_emb.append(time.perf_counter() - s)
    total_s = time.perf_counter() - t0

    rows, scores = [], {"genuine": [], "impostor": []}
    for fold, genuine, a, b in pairs:
        if a in embeddings and b in embeddings:
            score = engine.compare(embeddings[a], embeddings[b])
            scores["genuine" if genuine else "impostor"].append((fold, score))
            rows.append((fold, int(genuine), "/".join(map(str, a)), "/".join(map(str, b)), round(score, 6)))
        else:
            rows.append((fold, int(genuine), "/".join(map(str, a)), "/".join(map(str, b)), ""))

    g = np.array([s for _, s in scores["genuine"]])
    im = np.array([s for _, s in scores["impostor"]])
    candidates = np.unique(np.concatenate([g, im]))
    best = min(candidates, key=lambda t: abs(np.subtract(*rates(g, im, t, higher))))
    eer_far, eer_frr = rates(g, im, best, higher)

    # Operação a FAR alvo (com 3000 impostores a menor FAR mensurável é ~3.3e-4).
    ops = {}
    for target in (1e-2, 1e-3):
        ok = [t for t in candidates if rates(g, im, t, higher)[0] <= target]
        thr = (min(ok) if higher else max(ok)) if ok else None
        ops[str(target)] = None if thr is None else {
            "threshold": round(float(thr), 4),
            "far": round(rates(g, im, thr, higher)[0], 5),
            "frr": round(rates(g, im, thr, higher)[1], 5),
        }

    # Protocolo 10-fold: threshold escolhido em 9 folds, acurácia medida no fold restante.
    accs = []
    for k in range(10):
        gtr = np.array([s for f, s in scores["genuine"] if f != k])
        itr = np.array([s for f, s in scores["impostor"] if f != k])
        gte = np.array([s for f, s in scores["genuine"] if f == k])
        ite = np.array([s for f, s in scores["impostor"] if f == k])
        thr = max(candidates, key=lambda t: np.mean(np.concatenate([
            (gtr >= t) if higher else (gtr <= t), (itr < t) if higher else (itr > t)])))
        acc = np.mean(np.concatenate([
            (gte >= thr) if higher else (gte <= thr), (ite < thr) if higher else (ite > thr)]))
        accs.append(float(acc))

    reference = {"opencv": 0.363, "dlib": 0.6}[which]
    far_r, frr_r = rates(g, im, reference, higher)
    result = {
        "engine": engine.name, "model": engine.model, "metric": engine.metric,
        "images": len(needed), "failure_to_acquire": len(fta),
        "pairs_evaluated": {"genuine": len(g), "impostor": len(im)},
        "pairs_skipped_due_to_fta": len(pairs) - len(g) - len(im),
        "genuine_score": {"mean": float(g.mean()), "p5": float(np.percentile(g, 5)), "p50": float(np.median(g))},
        "impostor_score": {"mean": float(im.mean()), "p95": float(np.percentile(im, 95)), "p50": float(np.median(im))},
        "eer": {"threshold": round(float(best), 4), "far": round(eer_far, 5), "frr": round(eer_frr, 5)},
        "operating_points": ops,
        "reference_threshold": {"value": reference, "source": "OpenCV sample (cosine 0.363)" if which == "opencv" else "face_recognition/dlib tolerance 0.6 (e serviço legado 0.60)", "far": round(far_r, 5), "frr": round(frr_r, 5)},
        "legacy_threshold_0_5": None,
        "ten_fold_accuracy": {"mean": round(float(np.mean(accs)), 4), "std": round(float(np.std(accs)), 4)},
        "timing_ms": {
            "detect_mean": round(1000 * float(np.mean(t_det)), 2),
            "embed_mean": round(1000 * float(np.mean(t_emb)), 2),
            "total_s": round(total_s, 1),
        },
        "rss_mb_end": rss_mb(),
    }
    if which == "dlib":
        far5, frr5 = rates(g, im, 0.5, higher)
        result["legacy_threshold_0_5"] = {"far": round(far5, 5), "frr": round(frr5, 5)}
    else:
        del result["legacy_threshold_0_5"]

    out = ROOT / "results"
    (out / f"lfw_{which}.json").write_text(json.dumps(result, indent=2))
    with (out / f"lfw_{which}_scores.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fold", "genuine", "image_a", "image_b", "score"])
        w.writerows(rows)
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
