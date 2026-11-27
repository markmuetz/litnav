"""Step 1 of merging (hk27 docs/merging.md): candidate pairs of mentions across papers.

Each mention of a G1 run (g1run.records) gets an id `<key>#m<n>` and its k nearest mentions in other papers,
by the cosine of their statement embeddings. There is no similarity threshold: k is the only knob, and it is
set from measured recall (`recall`), not chosen. Output: candidates.jsonl in the run folder.
"""
from __future__ import annotations

import json
from pathlib import Path


def mentions(rd: Path) -> list[dict]:
    from . import g1run
    out = []
    for key, p in sorted(g1run.records(rd).items()):
        for n, m in enumerate(p["mentions"], 1):
            if str(m.get("statement", "")).strip():
                out.append({"id": f"{key}#m{n}", "key": key, **m})
    return out


def candidates(rd: Path, embedder, *, k: int = 10) -> Path:
    import numpy as np
    ms = mentions(rd)
    v = embedder.docs([m["statement"] for m in ms])
    v = v / np.linalg.norm(v, axis=1, keepdims=True).clip(1e-9)
    keys = np.array([m["key"] for m in ms])
    out = rd / "candidates.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for i in range(0, len(ms), 512):
            s = v[i:i + 512] @ v.T
            for j, row in enumerate(s):
                row[keys == keys[i + j]] = -2                     # other papers only
                top = np.argsort(-row)[:k]
                f.write(json.dumps({"id": ms[i + j]["id"], "near": [[ms[t]["id"], round(float(row[t]), 4)]
                                                                   for t in top if row[t] > -2]}) + "\n")
    m = json.loads((rd / "run.json").read_text())
    m["candidates"] = {"k": k, "embedder": getattr(embedder, "name", "?"), "mentions": len(ms)}
    (rd / "run.json").write_text(json.dumps(m, indent=1))
    return out


def recall(rd: Path, pairs: list[tuple[str, str]], ks=(1, 3, 5, 10, 20)) -> dict:
    """Of mention pairs judged the same claim, the share found within the first k candidates (either way)."""
    near = {}
    for l in (rd / "candidates.jsonl").read_text().splitlines():
        r = json.loads(l)
        near[r["id"]] = [x for x, _ in r["near"]]
    def rank(a, b):
        ra = near.get(a, []).index(b) if b in near.get(a, []) else 10**9
        rb = near.get(b, []).index(a) if a in near.get(b, []) else 10**9
        return min(ra, rb)
    rs = [rank(a, b) for a, b in pairs]
    return {"pairs": len(rs), **{f"at_{k}": round(sum(r < k for r in rs) / max(1, len(rs)), 3) for k in ks}}
