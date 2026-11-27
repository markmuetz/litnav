"""Steps 3-6 of merging (hk27 docs/merging.md): claims, support links, independent roots, status.

Input: a G1 run folder with candidates.jsonl and pair verdicts (merge.pairs). Output: graph.json in it.

- Claims (3): mentions joined by "same" verdicts, strongest pair first (embedding cosine), joining two
  groups only if every judged pair between them is "same" (complete linkage over the judged pairs: plain
  chains of "same" ran one claim through all 20 eval papers). Within-paper repeats are not merged (candidates are
  cross-paper only).
- Links (4): a shown mention's rests_on experiments support its claim; supports / contradicts / refines
  verdicts between mentions of different claims become claim-to-claim links, with their count.
- Roots (5): G0 run A draft 2's independence rules, applied only to what the index records:
  full (same root) - the same paper (a control, its perturbations and analyses of them); the same
  model code family (name with version numbers removed, or curation/model-families.toml); the same
  simulation suite, campaign case or archive; for observational evidence, the same dataset.
  partial (recorded, not merged) - a named physics scheme, a dataset used by model evidence, an algorithm
  or diagnostic, forcing data, shared authors.
- Status (6), rule STATUS_RULE: asserted (no shown mention), single source (shown in one paper), one line
  (shown in several papers, one root), multiple lines (two or more roots); contested when a contradicts
  link joins it to a claim that is shown. Counts are kept beside the label.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

STATUS_RULE = "status-1"
OBS_KINDS = {"observational_statistical_analysis", "instrument_measurement_retrieval", "feature_tracking"}
FULL_KINDS = {"model_code", "simulation_suite", "campaign_case", "archive"}
_VER = re.compile(r"\b(v(ersion)?\s*\.?\s*)?\d+(\.\d+)*[a-z]?\b|\(.*?\)", re.I)


def family(name: str, lookup: dict | None = None) -> str:
    n = (name or "").strip()
    if lookup and n.lower() in lookup:
        return lookup[n.lower()]
    n = _VER.sub(" ", n.lower())
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return " ".join(w for w in n.split() if w not in ("version", "model", "the")) or (name or "").lower()


def load_lookup(lib) -> dict:
    p = lib.root / "curation" / "model-families.toml" if lib else None
    if not p or not p.is_file():
        return {}
    import tomllib
    d = tomllib.loads(p.read_text())
    return {alias.lower(): fam for fam, v in d.items() for alias in [fam, *v.get("aliases", [])]}


class UF:
    def __init__(self, xs):
        self.p = {x: x for x in xs}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def claims(ms: list[dict], pairs: list[dict], sim: dict) -> dict[str, list[str]]:
    ids = [m["id"] for m in ms]
    uf = UF(ids)
    members = {i: {i} for i in ids}
    other = defaultdict(set)                       # judged pairs with any verdict but "same"
    for p in pairs:
        if p["verdict"] != "same":
            other[p["a"]].add(p["b"])
            other[p["b"]].add(p["a"])
    same = sorted((p for p in pairs if p["verdict"] == "same"),
                  key=lambda p: -sim.get(frozenset((p["a"], p["b"])), 0))
    for p in same:
        ra, rb = uf.find(p["a"]), uf.find(p["b"])
        if ra == rb:
            continue
        if any(other[x] & members[rb] for x in members[ra]):
            continue                                       # complete linkage: every judged pair is "same"
        uf.union(ra, rb)
        members[rb] |= members.pop(ra)
    return {f"c{n:05d}": sorted(v) for n, v in enumerate(sorted(members.values(), key=lambda v: (-len(v), min(v))), 1)}


def roots(exps: list[dict], authors: dict[str, set], lookup: dict) -> tuple[list[list[str]], list[dict]]:
    """Experiments (each with _key and _id) to groups that share a full root, and the partial links."""
    uf = UF([e["_id"] for e in exps])
    by = defaultdict(list)
    part = []
    for e in exps:
        by[("paper", e["_key"])].append(e["_id"])
        for r in e.get("shared_roots") or []:
            if not isinstance(r, dict) or not r.get("name"):
                continue
            k = r.get("kind")
            name = family(r["name"], lookup) if k == "model_code" else r["name"].strip().lower()
            full = k in FULL_KINDS or (k == "dataset" and e.get("evidence_kind") in OBS_KINDS)
            by[("full" if full else "partial", k, name)].append(e["_id"])
    for g, xs in by.items():
        if g[0] in ("paper", "full"):
            for x in xs[1:]:
                uf.union(xs[0], x)
        elif len({x.split("|")[0] for x in xs}) > 1:
            part.append({"kind": g[1], "name": g[2], "experiments": sorted(set(xs))})
    keys = sorted({e["_key"] for e in exps})
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if authors.get(a, set()) & authors.get(b, set()):
                part.append({"kind": "authors", "name": ", ".join(sorted(authors[a] & authors[b])), "papers": [a, b]})
    groups = defaultdict(list)
    for e in exps:
        groups[uf.find(e["_id"])].append(e["_id"])
    return sorted(map(sorted, groups.values())), part


def status(shown_papers: int, n_roots: int, contested: bool) -> str:
    s = ("asserted" if not shown_papers else "single source" if shown_papers == 1
         else "one line" if n_roots <= 1 else "multiple lines")
    return f"contested ({s})" if contested else s


def build(rd: Path, lib=None, *, results: str = "pairs-results.jsonl") -> dict:
    from . import g1run, merge
    recs = g1run.records(rd)
    ms = merge.mentions(rd)
    by_id = {m["id"]: m for m in ms}
    ps = merge.pairs(rd, results)
    sim = {}
    for l in (rd / "candidates.jsonl").read_text().splitlines():
        r = json.loads(l)
        for b, s in r["near"]:
            sim[frozenset((r["id"], b))] = s
    cl = claims(ms, ps, sim)
    of = {m: c for c, xs in cl.items() for m in xs}
    lookup = load_lookup(lib)
    authors = {}
    if lib:
        for k in recs:
            it = lib.get(k)
            authors[k] = {a.split(",")[0].strip().lower() for a in (getattr(it, "authors", None) or [])}
    exp = {}
    for k, p in recs.items():
        for e in p["experiments"]:
            for n in [e.get("name"), *(e.get("aliases") or [])]:
                if n:
                    exp.setdefault((k, n), {**e, "_key": k, "_id": f"{k}|{e.get('name')}"})
    links = Counter()
    for p in ps:
        ca, cb = of.get(p["a"]), of.get(p["b"])
        if p["verdict"] in ("supports", "contradicts", "refines") and ca and cb and ca != cb:
            links[(min(ca, cb), max(ca, cb), p["verdict"])] += 1
    contra = defaultdict(set)
    for (a, b, v) in links:
        if v == "contradicts":
            contra[a].add(b)
            contra[b].add(a)
    out = {}
    shown_of = {}
    for c, xs in cl.items():
        sh = [by_id[x] for x in xs if by_id[x].get("claim_type") == "shown"]
        shown_of[c] = {m["key"] for m in sh}
    for c, xs in cl.items():
        sh = [by_id[x] for x in xs if by_id[x].get("claim_type") == "shown"]
        es = {}
        for m in sh:
            for r in m.get("rests_on") or []:
                e = exp.get((m["key"], r))
                if e:
                    es[e["_id"]] = e
            if not m.get("rests_on"):                       # shown, experiment not named: the paper as root
                es.setdefault(f"{m['key']}|?", {"_key": m["key"], "_id": f"{m['key']}|?"})
        rg, part = roots(list(es.values()), authors, lookup) if es else ([], [])
        con = any(shown_of[o] for o in contra[c]) and bool(sh)
        rep = max(xs, key=lambda x: (sum(1 for p in ps if p["verdict"] == "same" and x in (p["a"], p["b"])), -len(by_id[x]["statement"])))
        out[c] = {"statement": by_id[rep]["statement"], "mentions": xs, "papers": sorted({by_id[x]["key"] for x in xs}),
                  "shown_papers": sorted(shown_of[c]), "experiments": sorted(es), "roots": rg, "partial": part,
                  "status": status(len(shown_of[c]), len(rg), con),
                  "claim_types": dict(Counter(by_id[x].get("claim_type") for x in xs))}
    g = {"rule": STATUS_RULE, "run": str(rd), "claims": out,
         "links": [{"a": a, "b": b, "relation": v, "pairs": n} for (a, b, v), n in sorted(links.items())]}
    (rd / "graph.json").write_text(json.dumps(g, ensure_ascii=False, indent=1))
    return g


def summary(g: dict) -> dict:
    cs = g["claims"].values()
    multi = [c for c in cs if len(c["papers"]) > 1]
    return {"claims": len(g["claims"]), "in_several_papers": len(multi),
            "largest": max((len(c["papers"]) for c in cs), default=0),
            "status": dict(Counter(c["status"] for c in cs)),
            "status_in_several_papers": dict(Counter(c["status"] for c in multi)),
            "links": dict(Counter(l["relation"] for l in g["links"]))}
