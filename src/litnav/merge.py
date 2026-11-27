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


# ---- step 2: same claim? (one call per mention, over its candidates not yet judged the other way)

VERDICTS = ("same", "supports", "contradicts", "refines", "different")

PAIR_SYSTEM = """You compare statements extracted from scientific papers on atmospheric convection and climate.
Each call gives several numbered pairs. In every pair, A is the same statement and B is a candidate from
another paper. For each pair, say how B relates to A:

- same: the same claim, about the same thing, in the same direction, under compatible conditions (region,
  season, model type, scale, period, and the rest). Wording and detail may differ. A is a restatement or a
  replication of the candidate, or the reverse.
- supports: a different claim that agrees with A and adds evidence for it, e.g. the same result under other
  conditions, or a mechanism that implies A.
- contradicts: the two cannot both hold under the conditions given: opposite direction, sign or conclusion.
  Opposite results under clearly different conditions are "refines", not "contradicts".
- refines: one narrows, qualifies or bounds the other (where or when it holds, by how much, an exception),
  without contradicting it.
- different: about another thing, or related only by topic.

Judge only from what the statements, their conditions and quotes say; do not use outside knowledge of what
is true. Direction matters: "X strengthens Y" and "X weakens Y" are not the same claim. Give each candidate's
pair's number, B's first five words copied exactly (so the answer can be matched to it), the verdict and
a reason of at most 20 words naming what A is about and what B is about."""

PAIR_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["pairs"], "properties": {
    "pairs": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                         "required": ["n", "starts", "verdict", "reason"], "properties": {
        "n": {"type": "integer"}, "starts": {"type": "string"}, "verdict": {"type": "string", "enum": list(VERDICTS)},
        "reason": {"type": "string"}}}}}}


def render(m: dict, title: str = "") -> str:
    cond = m.get("conditions")
    if isinstance(cond, list):
        cond = "; ".join(f"{c.get('dimension')}: {c.get('value')}" for c in cond if isinstance(c, dict))
    elif isinstance(cond, dict):
        cond = "; ".join(f"{k}: {v}" for k, v in cond.items())
    bits = [f"paper {m['key']}" + (f" ({title})" if title else ""), f"type {m.get('claim_type', '?')}",
            f"hedging {m.get('hedging', '?')}"]
    if cond:
        bits.append(f"conditions {cond}")
    q = str(m.get("quote") or "").strip()
    return f"{m['statement']}\n   [{'; '.join(bits)}]" + (f"\n   quote: \"{q[:400]}\"" if q else "")


def pair_jobs(lib, rd: Path, *, k: int = 20, per_call: int = 10, prefix: str = "pair", tag: str = "pairs") -> Path:
    """Jobs for each mention's first k candidates, `per_call` a call; an unordered pair is asked once."""
    ms = {m["id"]: m for m in mentions(rd)}
    titles = {key: (lib.get(key).title or "") for key in {m["key"] for m in ms.values()}}
    asked, js = set(), []
    for l in (rd / "candidates.jsonl").read_text().splitlines():
        r = json.loads(l)
        a = ms[r["id"]]
        cs = [b for b, _ in r["near"][:k] if frozenset((a["id"], b)) not in asked]
        if not cs:
            continue
        asked.update(frozenset((a["id"], b)) for b in cs)
        for part in range(0, len(cs), per_call):
            js.append(_pair_job(prefix, a, cs[part:part + per_call], ms, titles, part // per_call + 1))
    out = rd / f"{tag}-jobs.jsonl"
    out.write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in js), encoding="utf-8")
    return out


def _pair_job(prefix, a, cs, ms, titles, part):
    ra = render(a, titles[a["key"]])
    body = "\n\n".join(f"Pair {n}.\nA: {ra}\nB: {render(ms[b], titles[ms[b]['key']])}" for n, b in enumerate(cs, 1))
    return ({"id": f"{prefix}-{a['id']}" + (f"-{part}" if part > 1 else ""), "kind": "eval", "task": "pairs", "key": a["key"],
                   "keys": sorted({a["key"], *(ms[b]["key"] for b in cs)}), "a": a["id"], "candidates": cs,
                   "schema": PAIR_SCHEMA,
                   "messages": [{"role": "system", "content": PAIR_SYSTEM},
                                {"role": "user", "content": f"{body}\n\nFor each pair give n, the first five words of B, "
                                 "the verdict and the reason. Reply with JSON only."}]})


def outside_ok(lib, job: dict) -> bool:
    """A pair job quotes every paper in it: it may go out only if all of them are open."""
    from . import sharing as S
    return all(lib.get(k).allowed(S.SEND_OUT) for k in job.get("keys") or [job["key"]])


def _words(s: str, n: int = 5) -> list[str]:
    import re
    return re.findall(r"[a-z0-9]+", str(s).lower())[:n]


def aligned(answer: dict, statement: str) -> bool:
    """Does the answer's `starts` match the candidate it numbers? (An answer without it is taken as is.)"""
    if "starts" not in answer:
        return True
    a, b = _words(answer["starts"]), _words(statement)
    return bool(a) and a[:3] == b[:3]


def pairs(rd: Path, results: str = "pairs-results.jsonl", jobs: str = "pairs-jobs.jsonl", *, tag: str = "") -> list[dict]:
    """[{a, b, verdict, reason}] from a run of pair jobs; an answer with a bad number, or whose `starts`
    does not match the candidate it numbers, is dropped."""
    if tag:
        results, jobs = f"{tag}-results.jsonl", f"{tag}-jobs.jsonl"
    js = {j["id"]: j for j in map(json.loads, (rd / jobs).read_text().splitlines())}
    st = {m["id"]: m["statement"] for m in mentions(rd)}
    out = []
    for l in (rd / results).read_text().splitlines():
        r = json.loads(l)
        j = js.get(r["id"])
        o = r.get("output") if r.get("ok") else None
        if not j or not isinstance(o, dict):
            continue
        for p in o.get("pairs") or []:
            n = p.get("n")
            if (isinstance(n, int) and 1 <= n <= len(j["candidates"]) and p.get("verdict") in VERDICTS
                    and aligned(p, st.get(j["candidates"][n - 1], ""))):
                out.append({"a": j["a"], "b": j["candidates"][n - 1], "verdict": p["verdict"],
                            "reason": p.get("reason", ""), "rank": n})
    return out


def check_jobs(lib, rd: Path, *, per_verdict: int = 30, seed: int = 1, prefix: str = "check", tag: str = "pairs") -> Path:
    """A blind second opinion on a sample of pair verdicts, stratified by the first verdict: the same
    question, one pair a call, without the first answer. Run with a strong model; compare with `agreement`."""
    import random
    ms = {m["id"]: m for m in mentions(rd)}
    rnd = random.Random(seed)
    by = defaultdict_list()
    for p in pairs(rd, tag=tag):
        by[p["verdict"]].append(p)
    pick = [p for v in VERDICTS for p in rnd.sample(by[v], min(per_verdict, len(by[v])))]
    rnd.shuffle(pick)
    js = []
    for p in pick:
        a, b = ms[p["a"]], ms[p["b"]]
        js.append({"id": f"{prefix}-{p['a']}--{p['b']}", "kind": "eval", "key": a["key"], "keys": sorted({a["key"], b["key"]}),
                   "a": p["a"], "candidates": [p["b"]], "schema": PAIR_SCHEMA,
                   "messages": [{"role": "system", "content": PAIR_SYSTEM},
                                {"role": "user", "content": f"Pair 1.\nA: {render(a, lib.get(a['key']).title or '')}\n"
                                 f"B: {render(b, lib.get(b['key']).title or '')}\n\nReply with JSON only."}]})
    out = rd / f"{tag}-check-jobs.jsonl"
    out.write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in js), encoding="utf-8")
    return out


def defaultdict_list():
    from collections import defaultdict
    return defaultdict(list)


def agreement(rd: Path, tag: str = "pairs") -> dict:
    """First verdicts against the check's, pair by pair: a confusion table and kappa (on a sample stratified
    by the first verdict, so read the confusion rows, not the overall rate)."""
    first = {(p["a"], p["b"]): p["verdict"] for p in pairs(rd, tag=tag)}
    js = {j["id"]: j for j in map(json.loads, (rd / f"{tag}-check-jobs.jsonl").read_text().splitlines())}
    rows = []
    for l in (rd / f"{tag}-check-results.jsonl").read_text().splitlines():
        r = json.loads(l)
        j = js.get(r["id"])
        o = r.get("output") if r.get("ok") else None
        if j and isinstance(o, dict) and o.get("pairs"):
            rows.append((first[(j["a"], j["candidates"][0])], o["pairs"][0].get("verdict"), o["pairs"][0].get("reason", ""),
                         j["a"], j["candidates"][0]))
    from collections import Counter
    conf = Counter((a, b) for a, b, *_ in rows)
    n = len(rows)
    po = sum(a == b for a, b, *_ in rows) / max(1, n)
    fa, fb = Counter(a for a, *_ in rows), Counter(b for _, b, *_ in rows)
    pe = sum(fa[v] * fb[v] for v in VERDICTS) / max(1, n * n)
    same_p = [(a == "same", b == "same") for a, b, *_ in rows]
    return {"pairs": n, "agreement": round(po, 3), "kappa": round((po - pe) / (1 - pe), 3) if pe < 1 else None,
            "confusion": {f"{a} -> {b}": c for (a, b), c in sorted(conf.items(), key=lambda x: -x[1])},
            "same_precision": round(sum(a and b for a, b in same_p) / max(1, sum(a for a, _ in same_p)), 3),
            "rows": rows}
