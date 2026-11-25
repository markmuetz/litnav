"""Topics drawn from the literature (Mark, 25 Nov 2026). Each paper's aspects (what it reports in detail,
in an open vocabulary: no topic list is shown) are embedded and clustered by consensus: k-means at a sweep
of granularities and seeds, and two aspects share a topic when they fall together in most runs, so no
number of clusters is chosen. A topic recurs: it is reported by more than one paper. Topics are ranked by
papers, not aspects. A strong model names and merges them, citing their member papers.

Run folders: derived/topics/<stamp>[-label]/ (jobs.jsonl, results.jsonl, aspects.jsonl, clusters.json,
topics.json)."""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from . import sharing as S

PROMPT_VERSION = 1
CHARS = 200_000
SWEEP = (8, 12, 16, 24, 32, 48, 64, 96)      # granularities (method parameter; sensitivity can be reported)
SEEDS = (0, 1, 2)
AGREE = 0.5                                  # share of runs in which two aspects must fall together

SYSTEM = """You read one scientific paper and list the aspects it reports IN DETAIL: the methods it describes or
develops, the kinds of experiment or analysis it carries out, the phenomena it studies closely, the data it
builds or characterises, the models it configures. An aspect is reported in detail when the paper gives its
particulars (definitions, settings, procedures, values), not when it only names it or cites it.

For each aspect give:
- aspect: a short noun phrase in your own words, specific enough to tell it from neighbouring aspects;
- detail: one sentence on what the paper reports about it;
- sections: the titles of the sections where it is reported.
Usually 3 to 12 aspects. Write in your own words; do not copy sentences. Reply with JSON only."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["aspects"], "properties": {
    "aspects": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["aspect", "detail", "sections"],
                "properties": {"aspect": {"type": "string"}, "detail": {"type": "string"},
                               "sections": {"type": "array", "items": {"type": "string"}}}}}}}


def job(lib, it, chars: int = CHARS) -> dict:
    from .papereval import chunks_for
    ps = chunks_for(lib, it, chars)
    body = "\n\n".join(f"({p['section']})\n{p['text']}" for p in ps)
    return {"id": f"topics-{it.key}", "kind": "topics", "step": "aspects", "key": it.key, "schema": SCHEMA,
            "chars_sent": sum(len(p["text"]) for p in ps),
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": f"Paper: {it.title} ({it.year})\n\n{body}\n\nReply with JSON only."}]}


def new_run(lib, *, open_only: bool = True, label: str = "") -> Path:
    from . import localmodel
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    rd = lib.root / "derived" / "topics" / (stamp + (f"-{label}" if label else ""))
    rd.mkdir(parents=True, exist_ok=True)
    js = []
    for it in sorted(lib.items(), key=lambda i: i.key):
        if not (it.has_text and it.allowed(S.LOCAL_MODEL)) or (open_only and not it.allowed(S.SEND_OUT)):
            continue
        localmodel.gate(it)
        js.append(job(lib, it))
    (rd / "jobs.jsonl").write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in js))
    (rd / "run.json").write_text(json.dumps({"pass": "topics", "prompt_version": PROMPT_VERSION, "open_only": open_only,
                                             "papers": len(js),
                                             "created": dt.datetime.now().isoformat(timespec="seconds")}, indent=1))
    return rd


def aspects(rd: Path) -> list[dict]:
    out = []
    for l in (rd / "results.jsonl").read_text().splitlines():
        r = json.loads(l) if l.strip() else {}
        if r.get("ok"):
            for a in (r["output"] or {}).get("aspects", []):
                if a.get("aspect"):
                    out.append({"key": r["id"].removeprefix("topics-"), **a})
    return out


def consensus(m, *, sweep=SWEEP, seeds=SEEDS, agree=AGREE) -> list[int]:
    """Cluster labels from the co-association of rows across k-means runs (cosine). Rows that fall
    together in every run form atoms; atoms are merged by average linkage while the average share of runs
    in which their members fall together is at least `agree`. (Joining any pair above `agree`, single
    linkage, chains everything into one cluster: the first try, 25 Nov 2026.)"""
    import numpy as np
    from .field import kmeans
    n = len(m)
    ks = [k for k in sweep if k < n] or list(range(2, n))     # a small input: the granularities that fit
    labs = []
    for k in ks:
        for s_ in seeds:
            lab, _ = kmeans(m, k, seed=s_)
            labs.append(np.asarray(lab))
    if not labs:
        return list(range(n))
    L = np.stack(labs, axis=1)                                 # rows x runs
    atoms, inv = np.unique(L, axis=0, return_inverse=True)
    inv = np.asarray(inv).ravel()
    size = np.bincount(inv, minlength=len(atoms)).astype("float64")
    sim = np.zeros((len(atoms), len(atoms)))
    for r in range(L.shape[1]):
        sim += atoms[:, r][:, None] == atoms[:, r][None, :]
    sim /= L.shape[1]
    np.fill_diagonal(sim, -1.0)
    alive = np.ones(len(atoms), dtype=bool)
    group = np.arange(len(atoms))
    while True:
        s_ = np.where(alive[:, None] & alive[None, :], sim, -1.0)
        i, j = np.unravel_index(int(np.argmax(s_)), s_.shape)
        if s_[i, j] < agree:
            break
        ni, nj = size[i], size[j]
        sim[i, :] = (ni * sim[i, :] + nj * sim[j, :]) / (ni + nj)     # average linkage (Lance-Williams)
        sim[:, i] = sim[i, :]
        sim[i, i] = -1.0
        size[i] = ni + nj
        alive[j] = False
        group[group == j] = i
    roots = {}
    gl = [roots.setdefault(int(g), len(roots)) for g in group]
    return [gl[a] for a in inv]


def cluster(lib, rd: Path, embedder=None) -> dict:
    """clusters.json: consensus clusters of the aspects, those reported by more than one paper, ranked
    by papers; with the coverage curve (share of all aspects in the top n topics)."""
    import numpy as np
    if embedder is None:
        from .commands.searching import embedder_for
        embedder = embedder_for(lib)
    asp = aspects(rd)
    m = embedder.docs([f"{a['aspect']}: {a['detail']}" for a in asp])
    m = m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
    lab = consensus(m)
    groups = defaultdict(list)
    for a, l in zip(asp, lab):
        groups[l].append(a)
    cl = []
    for l, g in groups.items():
        keys = sorted({a["key"] for a in g})
        if len(keys) < 2:
            continue                      # a topic recurs across papers
        cl.append({"papers": len(keys), "aspects": len(g), "keys": keys,
                   "sample": [a["aspect"] for a in g][:40]})
    cl.sort(key=lambda c: -c["papers"])
    for i, c in enumerate(cl, 1):
        c["id"] = f"c{i:03d}"
    total = len(asp)
    cum, curve = 0, []
    for c in cl:
        cum += c["aspects"]
        curve.append(round(cum / max(1, total), 3))
    out = {"run": rd.name, "aspects": total, "papers": len({a["key"] for a in asp}),
           "singletons": total - sum(c["aspects"] for c in cl), "clusters": cl, "coverage_curve": curve,
           "method": {"sweep": list(SWEEP), "seeds": list(SEEDS), "agree": AGREE, "embedder": embedder.name}}
    (rd / "clusters.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


NAME_SYSTEM = """You are given clusters of aspects that scientific papers report in detail, each with the number of
papers reporting it and sample aspects (in a model's words). Name the topics they form: merge clusters that are
the same topic, keep apart those that are not, and drop none. A topic is something papers report in detail: a
method, a kind of experiment or analysis, a phenomenon, a kind of data or model. Use only the clusters, not what
you know of the field. For each topic give: name, description (one sentence), kind (method, experiment,
phenomenon, data, model, or other), and clusters (the ids it is made from). Reply with JSON only."""

NAME_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["topics"], "properties": {
    "topics": {"type": "array", "items": {"type": "object", "additionalProperties": False,
               "required": ["name", "description", "kind", "clusters"],
               "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                              "kind": {"type": "string",
                                       "enum": ["method", "experiment", "phenomenon", "data", "model", "other"]},
                              "clusters": {"type": "array", "items": {"type": "string"}}}}}}}


def name(rd: Path, *, model: str = "claude-fable-5-2") -> dict:
    """topics.json: the clusters named and merged by a strong model; each topic's papers are the union of
    its clusters' papers (so the ranking is by papers, computed by code)."""
    from .g0 import _claude
    cl = json.loads((rd / "clusters.json").read_text())
    lines = "\n".join(f"{c['id']} ({c['papers']} papers): " + "; ".join(c["sample"][:15]) for c in cl["clusters"])
    out = _claude(model, NAME_SYSTEM, f"Clusters ({len(cl['clusters'])}):\n{lines}", NAME_SCHEMA)
    by = {c["id"]: c for c in cl["clusters"]}
    topics = []
    for t in out.get("topics", []):
        ids = [i for i in t.get("clusters", []) if i in by]
        keys = sorted({k for i in ids for k in by[i]["keys"]})
        topics.append({**t, "clusters": ids, "papers": len(keys), "keys": keys})
    topics.sort(key=lambda t: -t["papers"])
    used = {i for t in topics for i in t["clusters"]}
    res = {"run": rd.name, "model": model, "topics": topics,
           "clusters_unassigned": sorted(set(by) - used)}
    (rd / "topics.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    return res
