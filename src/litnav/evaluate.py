"""Retrieval evaluation (`litnav eval`).

Questions live in the library, `eval/questions.toml`, never in this repo:

    [[question]]
    q = "what sets the angle of a squall line to the shear?"
    keys = ["smith2021cold"]              # papers a good answer must find
    passages = ["cold pool speed"]        # optional: text an expected passage contains
    kind = "paraphrase"                   # exact-name, paraphrase or concept
    written_from = "notes"                # notes, summary, memory: summaries favour
                                          # paper search, which embeds them

Configs: passage search in three modes, and paper search. Metrics at k=10:
recall (share of expected papers found) and MRR (1 / rank of the first hit).
A passage hit is a chunk from an expected paper that contains an expected
string after folding case, whitespace and hyphenation, so chunker changes do
not break the questions.

With 20-50 questions the means are noisy: `--compare` prints per-question
wins and losses and a sign test; differences under ~0.1 are noise.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import subprocess
import tomllib
from pathlib import Path

from . import chunks, index, search, sections
from . import sharing as S

K = 10
CONFIGS = ("keyword", "semantic", "hybrid", "papers")


def questions_path(lib) -> Path:
    return lib.root / "eval" / "questions.toml"


def load(lib) -> list[dict]:
    p = questions_path(lib)
    if not p.is_file():
        raise ValueError(f"no questions: write {p} (see `litnav eval --help`)")
    with open(p, "rb") as fh:
        qs = tomllib.load(fh).get("question", [])
    if not qs:
        raise ValueError(f"no questions yet in {p}: see the example there")
    for i, q in enumerate(qs):
        if not q.get("q") or not q.get("keys"):
            raise ValueError(f"question {i + 1} needs q and keys")
        q.setdefault("kind", "")
        q["keys"] = [lib.resolve(k) or k for k in q["keys"]]
    return qs


def fold(s: str) -> str:
    s = re.sub(r"-\s*\n\s*", "", s.lower())
    return re.sub(r"[\s\-‐–]+", " ", s).strip()


def _passage_hit(r: dict, q: dict) -> bool:
    if r["key"] not in q["keys"]:
        return False
    want = q.get("passages") or []
    return not want or any(fold(w) in fold(r["text"]) for w in want)


def score_one(results: list[dict], q: dict, level: str) -> dict:
    """recall@K and MRR@K for one question's ranked results."""
    if level == "passage":
        hits = [i for i, r in enumerate(results[:K]) if _passage_hit(r, q)]
        found = {results[i]["key"] for i in hits}
    else:
        ranked = list(dict.fromkeys(r["key"] for r in results))[:K]
        hits = [i for i, k in enumerate(ranked) if k in q["keys"]]
        found = {ranked[i] for i in hits}
    return {"recall": len(found) / len(set(q["keys"])),
            "mrr": 1 / (hits[0] + 1) if hits else 0.0}


def run(lib, embedder, configs=CONFIGS, log=print) -> dict:
    qs = load(lib)
    con = index.connect(lib, readonly=True)
    rows = []
    for q in qs:
        row = {"q": q["q"], "kind": q["kind"], "keys": q["keys"]}
        for c in configs:
            if c != "keyword" and embedder is None:
                continue
            if c == "papers":
                res = search.papers(con, q["q"], embedder=embedder, k=K, purpose=S.LOCAL)
                row["papers/paper"] = score_one(res, q, "paper")
            else:
                res = search.search(con, q["q"], embedder=embedder, mode=c, k=50, purpose=S.LOCAL)
                row[f"{c}/passage"] = score_one(res, q, "passage")
                row[f"{c}/paper"] = score_one(res, q, "paper")
        rows.append(row)
    return {"date": dt.date.today().isoformat(), "n": len(qs),
            "embedding_model": embedder.name if embedder else None,
            "chunks_version": chunks.VERSION, "sections_version": sections.VERSION,
            "litnav_git": _git_sha(), "rows": rows, "summary": summarise(rows)}


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent,
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def summarise(rows: list[dict]) -> dict:
    cols = sorted({c for r in rows for c in r if "/" in c})
    out = {}
    for c in cols:
        have = [r for r in rows if c in r]
        groups = {"all": have}
        for r in have:
            if r["kind"]:
                groups.setdefault(r["kind"], []).append(r)
        out[c] = {g: {"n": len(v),
                      "recall": round(sum(r[c]["recall"] for r in v) / len(v), 3),
                      "mrr": round(sum(r[c]["mrr"] for r in v) / len(v), 3)}
                  for g, v in groups.items() if v}
    return out


def report(res: dict) -> str:
    lines = [f"{res['n']} questions, k={K}, model {res['embedding_model']}, "
             f"chunks v{res['chunks_version']}, sections v{res['sections_version']}",
             f"{'config':22s} {'group':14s} {'n':>3s} {'recall':>7s} {'MRR':>6s}"]
    for c, groups in res["summary"].items():
        for g, m in groups.items():
            lines.append(f"{c:22s} {g:14s} {m['n']:3d} {m['recall']:7.3f} {m['mrr']:6.3f}")
    return "\n".join(lines)


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact sign test p-value (ties dropped)."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def compare(a: dict, b: dict, metric: str = "mrr") -> str:
    """Per-config wins/ties/losses of run b against run a, matched by question."""
    ra = {r["q"]: r for r in a["rows"]}
    lines = [f"b vs a on {metric}: wins / ties / losses, mean difference, sign test p"]
    cols = sorted({c for r in b["rows"] for c in r if "/" in c})
    for c in cols:
        w = t = l = 0
        diffs = []
        for r in b["rows"]:
            o = ra.get(r["q"])
            if not o or c not in o or c not in r:
                continue
            d = r[c][metric] - o[c][metric]
            diffs.append(d)
            if abs(d) < 1e-9:
                t += 1
            elif d > 0:
                w += 1
            else:
                l += 1
        if diffs:
            mean = sum(diffs) / len(diffs)
            lines.append(f"{c:22s} {w:3d} / {t:3d} / {l:3d}  {mean:+.3f}  p={sign_test(w, l):.2f}"
                         + ("" if abs(mean) >= 0.1 else "  (under 0.1: noise)"))
    return "\n".join(lines)


def save(res: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, indent=1) + "\n")
