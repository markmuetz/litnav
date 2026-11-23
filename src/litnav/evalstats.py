"""Statistics for the model evals: intervals on scores and paired comparisons.

Scores from a few dozen items carry wide uncertainty, so every score gets an
interval, and two models are compared item by item (McNemar's test on the
items where exactly one of them is right), not by their totals.
"""
from __future__ import annotations

import math
import random


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """95% Wilson score interval for k successes out of n."""
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)]


def mcnemar(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value: b items only the first got right,
    c only the second (a binomial test of b against b + c at p = 1/2)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def paired(a: dict, b: dict) -> dict:
    """Compare two runs item by item. a, b: item id -> right (bool). Only
    items present in both count."""
    ids = sorted(set(a) & set(b))
    both = sum(a[i] and b[i] for i in ids)
    a_only = [i for i in ids if a[i] and not b[i]]
    b_only = [i for i in ids if b[i] and not a[i]]
    return {"n": len(ids), "both": both, "a_only": len(a_only), "b_only": len(b_only),
            "neither": len(ids) - both - len(a_only) - len(b_only),
            "a": sum(a[i] for i in ids), "b": sum(b[i] for i in ids),
            "p_mcnemar": mcnemar(len(a_only), len(b_only)),
            "a_only_ids": a_only, "b_only_ids": b_only}


def bootstrap_ratio(pairs: list[tuple[float, float]], *, reps: int = 2000, seed: int = 0) -> list[float] | None:
    """95% interval for sum(num) / sum(den), resampling the units (papers):
    statements within a paper are not independent, papers roughly are."""
    pairs = [p for p in pairs if p[1]]
    if not pairs:
        return None
    rng = random.Random(seed)
    stats = []
    for _ in range(reps):
        s = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        stats.append(sum(x for x, _ in s) / sum(y for _, y in s))
    stats.sort()
    return [round(stats[int(0.025 * reps)], 3), round(stats[int(0.975 * reps) - 1], 3)]


def bootstrap_diff(a: dict, b: dict, *, reps: int = 2000, seed: int = 0) -> dict | None:
    """Paired bootstrap over shared units of ratio(a) - ratio(b). a, b: unit ->
    (num, den)."""
    ids = sorted(i for i in set(a) & set(b) if a[i][1] and b[i][1])
    if not ids:
        return None

    def diff(sample):
        return (sum(a[i][0] for i in sample) / sum(a[i][1] for i in sample)
                - sum(b[i][0] for i in sample) / sum(b[i][1] for i in sample))
    rng = random.Random(seed)
    stats = sorted(diff([ids[rng.randrange(len(ids))] for _ in ids]) for _ in range(reps))
    return {"n": len(ids), "diff": round(diff(ids), 3),
            "ci": [round(stats[int(0.025 * reps)], 3), round(stats[int(0.975 * reps) - 1], 3)]}
