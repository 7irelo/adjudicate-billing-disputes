"""Statistics for small samples. Every comparison reports an interval, not just a point."""

from collections.abc import Sequence
from itertools import pairwise

import numpy as np


def bootstrap_ci(
    values: Sequence[float] | np.ndarray,
    resamples: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float, float]:
    """(mean, low, high). Percentile bootstrap, seeded so reports are reproducible."""
    data = np.asarray(values, dtype=float)
    if data.size == 0:
        return (float("nan"), float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = data[rng.integers(0, data.size, size=(resamples, data.size))].mean(axis=1)
    low, high = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(data.mean()), float(low), float(high)


def paired_bootstrap_diff(
    a: Sequence[float], b: Sequence[float], resamples: int = 10_000, seed: int = 0
) -> tuple[float, float, float]:
    """The mean and 95% CI of b - a, resampling cases so the pairing is kept."""
    if len(a) != len(b):
        raise ValueError("paired samples must have equal length")
    return bootstrap_ci(np.asarray(b, float) - np.asarray(a, float), resamples, seed=seed)


def cohens_kappa(a: Sequence, b: Sequence) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("kappa needs two equal, non-empty label lists")
    labels = sorted(set(a) | set(b), key=str)
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    expected = sum((list(a).count(c) / n) * (list(b).count(c) / n) for c in labels)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def quadratic_weighted_kappa(a: Sequence[int], b: Sequence[int], levels: int) -> float:
    observed = np.zeros((levels, levels))
    for x, y in zip(a, b, strict=True):
        observed[x, y] += 1
    weights = np.array([[(i - j) ** 2 for j in range(levels)] for i in range(levels)], float)
    expected = np.outer(observed.sum(0), observed.sum(1)) / max(observed.sum(), 1)
    denominator = float((weights * expected).sum())
    return 1.0 if denominator == 0 else 1 - float((weights * observed).sum()) / denominator


def expected_calibration_error(
    probs: Sequence[float], correct: Sequence[bool], bins: int = 10
) -> float:
    p, c = np.asarray(probs, float), np.asarray(correct, float)
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for low, high in pairwise(edges):
        mask = (p > low) & (p <= high)
        if mask.any():
            total += mask.mean() * abs(p[mask].mean() - c[mask].mean())
    return float(total)
