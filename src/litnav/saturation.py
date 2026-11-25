"""Saturation of an induction sample: is the number of papers inventoried enough?

Take the inventories already done, in random order, and count the distinct kinds (of detail for a topic
template; of key fact, condition, shared root and paper kind for G0) seen after each paper. If the curve
flattens well before the sample size, more papers would mostly repeat known kinds; if it still rises, the
sample is too small. Kinds are consensus clusters of the names (as in the tallies); because that grouping is a
method choice, the curve is given at two granularities (agreement 0.5 and 0.8: the finer one counts more
kinds). Chao1 (Chao 1984, from the kinds seen in one and in two papers) estimates how many kinds the
population holds, so share_seen says what part of them the sample found. Code only: local embeddings, no model calls. Writes saturation.json in the run folder."""
from __future__ import annotations

import json
import random
from pathlib import Path

AGREES = (0.5, 0.8)
ORDERS = 50


def rows_template(rd: Path) -> dict[str, list[tuple[str, str]]]:
    from .templates import inventories
    return {"details": [(r["key"], d["detail"]) for r in inventories(rd) if r.get("reports_topic", True)
                        for d in r.get("details", [])]}


def rows_g0(rd: Path) -> dict[str, list[tuple[str, str]]]:
    inv = [json.loads(l) for l in (rd / "inventories.clean.jsonl").read_text().splitlines() if l.strip()]
    out: dict[str, list] = {"key_facts": [], "conditions": [], "shared_roots": [], "paper_kind": []}
    for r in inv:
        k = r["key"]
        for e in r.get("experiments") or []:
            out["key_facts"] += [(k, f["fact"]) for f in e.get("key_facts") or [] if f.get("fact")]
        out["conditions"] += [(k, c["dimension"]) for c in r.get("conditions") or [] if c.get("dimension")]
        out["shared_roots"] += [(k, s["shared"]) for s in r.get("independence") or [] if s.get("shared")]
        if r.get("paper_kind"):
            out["paper_kind"].append((k, r["paper_kind"]))
    return out


def curve(rows: list[tuple[str, str]], labels: list[int], *, orders: int = ORDERS, seed: int = 0) -> dict:
    """Kinds seen after n papers (mean, min, max over random orders), all kinds and recurring ones (kinds
    that more than one paper reports in the whole sample: a singleton kind can't be told from noise)."""
    by: dict[str, set] = {}
    papers_of: dict[int, set] = {}
    for (k, _), l in zip(rows, labels):
        by.setdefault(k, set()).add(l)
        papers_of.setdefault(l, set()).add(k)
    recurring = {l for l, ks in papers_of.items() if len(ks) > 1}
    keys = sorted(by)
    rng = random.Random(seed)
    allc, recc = [], []
    for _ in range(orders):
        o = keys[:]
        rng.shuffle(o)
        seen: set = set()
        a, r = [], []
        for k in o:
            seen |= by[k]
            a.append(len(seen))
            r.append(len(seen & recurring))
        allc.append(a)
        recc.append(r)
    n = len(keys)
    stat = lambda cs: [{"n": i + 1, "mean": round(sum(c[i] for c in cs) / len(cs), 1), "min": min(c[i] for c in cs),
                        "max": max(c[i] for c in cs)} for i in range(n)]
    sa, sr = stat(allc), stat(recc)
    tail = max(1, n // 5)                               # new kinds per paper over the last fifth of the sample
    rate = lambda s: round((s[-1]["mean"] - s[-1 - tail]["mean"]) / tail, 2) if n > tail else None
    f1 = sum(len(ks) == 1 for ks in papers_of.values())     # kinds seen in exactly one paper
    f2 = sum(len(ks) == 2 for ks in papers_of.values())     # ... in exactly two
    # Chao1 (Chao 1984; incidence form): estimated number of kinds in the population the sample is drawn
    # from; bias-corrected form when f2 = 0
    chao1 = len(papers_of) + (f1 * f1 / (2 * f2) if f2 else f1 * (f1 - 1) / 2) * (n - 1) / n
    return {"papers": n, "kinds": len(papers_of), "recurring": len(recurring), "f1": f1, "f2": f2,
            "chao1": round(chao1, 1), "share_seen": round(len(papers_of) / chao1, 2) if chao1 else None,
            "new_per_paper_last_fifth": rate(sa), "new_recurring_per_paper_last_fifth": rate(sr),
            "all": sa, "recurring_curve": sr}


def run(lib, rd: Path, embedder=None) -> dict:
    import numpy as np
    from .topics import consensus
    if embedder is None:
        from .commands.searching import embedder_for
        embedder = embedder_for(lib)
    rd = Path(rd)
    sets = rows_g0(rd) if (rd / "inventories.clean.jsonl").is_file() else rows_template(rd)
    out = {"run": rd.name, "method": {"agrees": list(AGREES), "orders": ORDERS, "embedder": embedder.name}, "items": {}}
    for name, rows in sets.items():
        if len(rows) < 3:
            continue
        m = embedder.docs([t for _, t in rows])
        m = m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
        out["items"][name] = {str(a): curve(rows, consensus(m, agree=a)) for a in AGREES}
    (rd / "saturation.json").write_text(json.dumps(out, indent=1))
    return out
