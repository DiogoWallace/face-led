"""Métricas de verificação 1:1. Funções puras sobre arrays de similaridade.

Convenção das portas: maior similaridade = mais parecido. Um par é aceito
quando `score >= threshold`.

- FAR (false accept rate): fração de pares impostores aceitos.
- FRR (false reject rate): fração de pares genuínos rejeitados.
- EER: ponto em que FAR e FRR se igualam (o mais próximo entre os candidatos).

Os thresholds calculados aqui descrevem um dataset. Não são calibração de
produção: a FaceMatchPolicy só recebe threshold por configuração (ADR-003).
"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class OperatingPoint:
    threshold: float
    far: float
    frr: float


def _sorted(values) -> np.ndarray:
    array = np.sort(np.asarray(values, dtype=np.float64))
    if array.size == 0:
        raise ValueError("conjunto de scores vazio")
    return array


def _far_frr(genuine: np.ndarray, impostor: np.ndarray, thresholds: np.ndarray):
    """FAR e FRR para cada threshold, com genuine/impostor já ordenados."""
    accepted_impostors = impostor.size - np.searchsorted(impostor, thresholds, side="left")
    rejected_genuine = np.searchsorted(genuine, thresholds, side="left")
    return accepted_impostors / impostor.size, rejected_genuine / genuine.size


def rates(genuine, impostor, threshold: float) -> OperatingPoint:
    g, i = _sorted(genuine), _sorted(impostor)
    far, frr = _far_frr(g, i, np.array([threshold]))
    return OperatingPoint(threshold=float(threshold), far=float(far[0]), frr=float(frr[0]))


def equal_error_rate(genuine, impostor) -> OperatingPoint:
    g, i = _sorted(genuine), _sorted(impostor)
    candidates = np.unique(np.concatenate([g, i]))
    far, frr = _far_frr(g, i, candidates)
    k = int(np.argmin(np.abs(far - frr)))
    return OperatingPoint(threshold=float(candidates[k]), far=float(far[k]), frr=float(frr[k]))


def threshold_at_far(genuine, impostor, target_far: float) -> OperatingPoint | None:
    """Menor threshold com FAR <= alvo (o que minimiza a FRR sob essa FAR)."""
    g, i = _sorted(genuine), _sorted(impostor)
    candidates = np.unique(np.concatenate([g, i, [np.nextafter(i[-1], np.inf)]]))
    far, frr = _far_frr(g, i, candidates)
    ok = np.flatnonzero(far <= target_far)
    if ok.size == 0:
        return None
    k = int(ok[0])
    return OperatingPoint(threshold=float(candidates[k]), far=float(far[k]), frr=float(frr[k]))


def _best_accuracy_threshold(genuine: np.ndarray, impostor: np.ndarray) -> float:
    candidates = np.unique(np.concatenate([genuine, impostor]))
    far, frr = _far_frr(genuine, impostor, candidates)
    correct = (1 - frr) * genuine.size + (1 - far) * impostor.size
    return float(candidates[int(np.argmax(correct))])


def ten_fold_accuracy(folds: np.ndarray, genuine_mask: np.ndarray, scores: np.ndarray):
    """Protocolo LFW: threshold escolhido em 9 folds, acurácia medida no restante.

    Devolve (média, desvio padrão, acurácias por fold).
    """
    folds, genuine_mask, scores = map(np.asarray, (folds, genuine_mask, scores))
    accuracies = []
    for k in np.unique(folds):
        train, test = folds != k, folds == k
        threshold = _best_accuracy_threshold(
            np.sort(scores[train & genuine_mask]), np.sort(scores[train & ~genuine_mask])
        )
        predicted_same = scores[test] >= threshold
        accuracies.append(float(np.mean(predicted_same == genuine_mask[test])))
    return float(np.mean(accuracies)), float(np.std(accuracies)), accuracies


def summarize(values) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0:
        return {}
    p1, p5, p50, p95, p99 = np.percentile(array, [1, 5, 50, 95, 99])
    return {
        "count": int(array.size),
        "mean": float(array.mean()),
        "std": float(array.std()),
        "min": float(array.min()),
        "p1": float(p1),
        "p5": float(p5),
        "p50": float(p50),
        "p95": float(p95),
        "p99": float(p99),
        "max": float(array.max()),
    }


def histogram(values, bins: int = 20, value_range=(-0.2, 1.0)) -> dict[str, list[float]]:
    counts, edges = np.histogram(np.asarray(values, dtype=np.float64), bins=bins, range=value_range)
    return {"edges": [round(float(e), 4) for e in edges], "counts": [int(c) for c in counts]}
