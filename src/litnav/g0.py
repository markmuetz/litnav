"""G0: induce a library's domain schema from its papers, knowing nothing about the field.

The claim graph has a fixed core that is the same in any field (claims, how a
paper relates to each, the evidence and experiments they rest on, independence,
hedging, conditions, citations). Everything else (what counts as an experiment
here, which facts classify its evidence, the vocabulary of conditions, the
topics papers report in detail) is the domain schema. G0 reads it from the
literature instead of having an expert design it.

Steps (`litnav g0 ...`):

  sample      choose about 100 papers for inventories, stratified by embedding
              cluster and year (open papers included on purpose: the synthesis
              may read them)
  push        survey jobs (every paper, from its public abstract, else its
              summary) and inventory jobs (the sample, from its text) as one
              batch on the cluster spool, for the local model;
              `litnav queue pull` brings the results back into the run folder
  tally       counts across papers, and the inventories with anything copied
              from the paper (a run of 12+ words) removed: the bundle
  synthesize  Fable drafts the domain schema and the general (G1) prompt from
              the core, the purpose, the bundle and a few open papers' text
  review      Opus and Gemini review the draft against the same evidence
  revise      Fable revises the draft from both reviews

Every prompt is free of domain words: the run is blind to the hand-built schema.
What goes to Claude or Gemini is metadata, model-written tallies and inventories
(no passages) and open papers' text only.

Run folders: derived/g0/<run>/ (git-ignored: jobs hold paper text).
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import abstracts, copyfix, derived
from . import sharing as S

PROMPT_VERSION = 1
SURVEY_CHARS = 6_000             # a paper with neither abstract nor summary: the start of its text
INVENTORY_CHARS = 72_000         # as the paper eval: fits the local model's context with room to think
COPY_WORDS = 12                  # the copy check: a run this long from the paper is dropped before Claude sees it
FABLE = "claude-fable-5-2"
OPUS = "claude-opus-5-5"
GEMINI = "gemini-4-neon-high"

CORE = """\
The claim graph is built on a fixed core, the same in any field of science. It is epistemology: how
knowledge is claimed and supported.

- Claim: an assertion about the world, in general form. The unit the graph reasons about.
- Mention: a claim as one paper makes it, with the passage it is in, its conditions and its wording.
  Mentions are what is extracted from papers; claims are merged from mentions.
- Claim type: how the paper relates to the claim. shown (the paper's own work demonstrates it),
  restated (attributed to other work, or background), assumed (a premise or a choice of method),
  speculation (offered without support). Only shown mentions are new evidence.
- Evidence and experiments: a shown mention rests on one or more experiments. An experiment is
  something a paper did that produced results. Experiments use things (instruments, data, tools,
  methods, materials, subjects).
- Support: links from evidence to claims (supports, contradicts, refines, depends on).
- Independence: whether two pieces of support share roots (the same experiment, the same data, the
  same tool, the same group). Support is counted in independent roots, not in mentions or citations.
- Status: what the support adds up to (asserted, single source, robust within one line of evidence,
  several independent lines, contested), computed by a rule.
- Hedging: how firmly the paper states a mention.
- Conditions: the qualifiers that limit where a claim holds. The idea is fixed; the vocabulary is not.
- Citations: which work a mention attributes a claim to.
- Provenance: which pass, prompt and model made each record.

The ontology follows from this core and is a graph, not a hierarchy.
"""

PURPOSE = """\
The graph is an instrument for three questions about the state of the evidence in the corpus:
1. What is contested: claims with independent support on both sides.
2. What is poorly supported: claims widely restated but resting on few independent experiments.
3. What is foundational but weak: claims many others depend on whose own support is thin.
"""

# ---- prompts for the local model (domain-free)

SURVEY_SYSTEM = (
    "You tag academic papers for a map of a body of literature. You know nothing in advance about the "
    "field: describe each paper in its own terms. From the title and the text given, reply with:\n"
    "- paper_kind: what kind of work it is, in 1-5 words of your own.\n"
    "- studies: 1-6 short tags for what the paper is about (the things, processes, places or populations "
    "it studies).\n"
    "- approach: 1-4 short tags for how it gets its results (what it does: e.g. measures, simulates, "
    "derives, analyses existing records, reviews other work, builds a tool).\n"
    "- uses: the named things it uses to get them (data, instruments, tools, models, methods, materials), "
    "each with its name as written and what kind of thing it is in 1-4 words of your own.\n"
    "- conditions: short tags for where, when or under what circumstances its results apply.\n"
    "Tags are short phrases in lower case. Don't copy sentences. Reply with JSON only.")

SURVEY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["paper_kind", "studies", "approach", "uses", "conditions"],
    "properties": {
        "paper_kind": {"type": "string"},
        "studies": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
        "approach": {"type": "array", "maxItems": 4, "items": {"type": "string"}},
        "uses": {"type": "array", "maxItems": 12, "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "kind"],
            "properties": {"name": {"type": "string"}, "kind": {"type": "string"}}}},
        "conditions": {"type": "array", "maxItems": 8, "items": {"type": "string"}}}}

INVENTORY_SYSTEM = (
    "You read an academic paper to help work out what a database of scientific claims should record "
    "about papers in its field. You know nothing in advance about the field and must not assume what "
    "matters in it: find out from the paper. You see its title, then numbered passages. Answer in your "
    "own words (short phrases, no copied sentences); names and values are given as written. Reply with:\n"
    "- paper_kind: what kind of work this is (1-5 words). contribution: what the paper itself adds, in one "
    "or two sentences.\n"
    "- experiments: each distinct piece of work the paper itself did that produced results (whatever form "
    "that takes in this field: a measurement campaign, a trial, a survey, a simulation, an analysis of "
    "existing records, a derivation, ...). For each: name (the paper's own name for it, or a short "
    "description); what (one sentence); produces (what kind of result it gives); uses (each thing it "
    "uses: name as written, and what kind of thing it is in a few words); key_facts (the facts about this "
    "experiment that someone would need in order to judge how far to trust its results, or to compare "
    "them with another paper's: for each, the fact in a few general words, its value here as written, "
    "and why it matters in a few words); passages (ids).\n"
    "- claims: up to 8 of the paper's main claims: statement (general form, own words); basis (shown: its "
    "own experiments demonstrate it; restated: from other work or background; assumed: a premise or "
    "choice of method; speculation); rests_on (names of the experiments above it rests on, if shown); "
    "passages.\n"
    "- independence: what someone would need to know to tell whether this paper's evidence is "
    "independent of other papers' evidence (things that could be shared: data, tools, methods, people, "
    "assumptions). For each, what could be shared, in general words, and what it is here, as written.\n"
    "- conditions: the conditions under which the results hold. For each, the dimension in general words "
    "and its value here as written.\n"
    "- terms: up to 10 terms the paper relies on that a reader from outside the field would need "
    "explained, each with its meaning in your own words.\n"
    "- reliability_checks: what a specialist in this field would check first to judge whether these "
    "results are sound (up to 6, own words).\n"
    "- missing: facts the paper does not state that one would need to compare its results with others "
    "(up to 5).\n"
    "Reply with JSON only.")


def inventory_schema(ids: list[str]) -> dict:
    s = {"type": "string"}
    pas = {"type": "array", "maxItems": 4, "items": {"type": "string", "enum": ids}}
    strs = lambda n: {"type": "array", "maxItems": n, "items": s}

    def obj(**props):
        return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}

    exp = obj(name=s, what=s, produces=s,
              uses={"type": "array", "maxItems": 12, "items": obj(name=s, kind=s)},
              key_facts={"type": "array", "maxItems": 12, "items": obj(fact=s, value=s, why=s)},
              passages=pas)
    claim = obj(statement=s, basis={"type": "string", "enum": ["shown", "restated", "assumed", "speculation"]},
                rests_on=strs(6), passages=pas)
    return obj(paper_kind=s, contribution=s,
               experiments={"type": "array", "maxItems": 10, "items": exp},
               claims={"type": "array", "maxItems": 8, "items": claim},
               independence={"type": "array", "maxItems": 8, "items": obj(shared=s, here=s)},
               conditions={"type": "array", "maxItems": 10, "items": obj(dimension=s, value=s)},
               terms={"type": "array", "maxItems": 10, "items": obj(term=s, meaning=s)},
               reliability_checks=strs(6), missing=strs(5))


# ---- runs

def run_root(lib) -> Path:
    return lib.root / "derived" / "g0"


def new_run(lib, label: str = "") -> Path:
    rd = run_root(lib) / (dt.datetime.now().strftime("%Y-%m-%d-%H%M") + (f"-{label}" if label else ""))
    rd.mkdir(parents=True, exist_ok=False)
    return rd


def latest(lib) -> Path:
    runs = sorted(p for p in run_root(lib).glob("*") if p.is_dir())
    if not runs:
        raise FileNotFoundError("no G0 run yet: `litnav g0 sample`")
    return runs[-1]


def _jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]


def _write_jsonl(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


# ---- sample

def _usable(it) -> bool:
    return it.allowed(S.LOCAL_MODEL) and it.has_text


def _clusters(lib, keys: list[str], k: int, seed: int) -> dict[str, int]:
    """Embedding clusters of library papers (from the field store's vectors); {} if there are none."""
    try:
        from . import field
        import numpy as np
        con = field.connect(lib)
        ids, srcs, m = field._matrix(con)
        lk = dict(con.execute("SELECT id, key FROM library").fetchall())
        con.close()
    except Exception:
        return {}
    rows = [(lk.get(i), j) for j, (i, s) in enumerate(zip(ids, srcs)) if s == "library" and lk.get(i) in keys]
    if len(rows) < k * 3:
        return {}
    lab, _ = field.kmeans(m[np.array([j for _, j in rows])], k, seed=seed)
    return {key: int(c) for (key, _), c in zip(rows, lab)}


def _year_bin(y) -> str:
    try:
        y = int(str(y)[:4])
    except ValueError:
        return "unknown"
    return "<2000" if y < 2000 else "2000s" if y < 2010 else "2010-17" if y < 2018 else "2018+"


def stratified(strata: dict[str, list[str]], n: int, rng: random.Random) -> list[str]:
    """Proportional allocation with at least one paper from every stratum (largest remainders)."""
    total = sum(len(v) for v in strata.values())
    if total <= n:
        return sorted(k for v in strata.values() for k in v)
    quota = {s: max(1, n * len(v) / total) for s, v in strata.items() if v}
    take = {s: int(q) for s, q in quota.items()}
    for s in sorted(quota, key=lambda s: quota[s] - take[s], reverse=True):
        if sum(take.values()) >= n:
            break
        take[s] += 1
    out = []
    for s, v in strata.items():
        v = sorted(v)
        rng.shuffle(v)
        out += v[:take.get(s, 0)]
    return sorted(out)


def sample(lib, n: int = 100, *, k: int = 12, seed: int = 1, label: str = "") -> Path:
    items = [it for it in lib.items() if _usable(it)]
    keys = [it.key for it in items]
    cl = _clusters(lib, keys, k, seed)
    strata = defaultdict(list)
    for it in items:
        strata[f"c{cl.get(it.key, '-')}|{_year_bin(it.year)}"].append(it.key)
    chosen = stratified(dict(strata), n, random.Random(seed))
    rd = new_run(lib, label)
    by = {it.key: it for it in items}
    _write_jsonl(rd / "sample.jsonl", [{"key": k_, "year": by[k_].year, "open": by[k_].allowed(S.SEND_OUT),
                                        "cluster": cl.get(k_), "stratum": next(s for s, v in strata.items()
                                                                              if k_ in v)} for k_ in chosen])
    (rd / "run.json").write_text(json.dumps({
        "pass": "G0", "prompt_version": PROMPT_VERSION, "created": dt.datetime.now().isoformat(timespec="seconds"),
        "sample": {"n": len(chosen), "asked": n, "clusters": k if cl else 0, "seed": seed,
                   "pool": len(items), "strata": len(strata)}}, indent=1))
    return rd


# ---- jobs

def survey_input(lib, it) -> tuple[str, str]:
    a = abstracts.public(it)
    if a:
        return a, "abstract"
    s = it.summary()
    if s and s.get("summary"):
        return s["summary"], "summary"
    return it.text()[:SURVEY_CHARS], "text"


def survey_job(lib, it) -> dict:
    body, src = survey_input(lib, it)
    return {"id": f"g0-survey-{it.key}", "kind": "g0", "step": "survey", "key": it.key, "source": src,
            "schema": SURVEY_SCHEMA,
            "messages": [{"role": "system", "content": SURVEY_SYSTEM},
                         {"role": "user", "content": f"Title: {it.title} ({it.year})\n\n{body}\n\nReply with "
                                                     "JSON only."}]}


def inventory_job(lib, it, chars: int = INVENTORY_CHARS) -> dict:
    from .papereval import chunks_for
    ps = chunks_for(lib, it, chars)
    body = "\n\n".join(f"[{p['id']}] ({p['section']})\n{p['text']}" for p in ps)
    ids = [p["id"] for p in ps]
    return {"id": f"g0-inventory-{it.key}", "kind": "g0", "step": "inventory", "key": it.key,
            "schema": inventory_schema(ids), "chars_sent": sum(len(p["text"]) for p in ps),
            "messages": [{"role": "system", "content": INVENTORY_SYSTEM},
                         {"role": "user", "content": f"Paper: {it.title} ({it.year})\n\nPassages:\n{body}"
                                                     "\n\nReply with JSON only."}]}


def jobs(lib, rd: Path, *, survey: bool = True) -> list[dict]:
    from . import localmodel
    out = []
    for r in _jsonl(rd / "sample.jsonl"):
        it = lib.get(r["key"])
        localmodel.gate(it)
        out.append(inventory_job(lib, it))
    if survey:
        out += [survey_job(lib, it) for it in sorted(lib.items(), key=lambda i: i.key)
                if it.allowed(S.LOCAL_MODEL) and (abstracts.public(it) or it.has_text)]
    return out


def push(lib, rd: Path, *, submit: bool = True, log=print) -> dict:
    from . import spool
    js = jobs(lib, rd)
    _write_jsonl(rd / "jobs.jsonl", js)
    m = json.loads((rd / "run.json").read_text())
    m["pushed"] = dt.datetime.now().isoformat(timespec="seconds")
    m["jobs"] = Counter(j["step"] for j in js)
    (rd / "run.json").write_text(json.dumps(m, indent=1))
    return spool.push(lib, js, kind="g0", meta={"run_dir": str(rd)}, submit=submit, log=log)


def import_batch(lib, b: dict, jobs: dict, fresh: list, server: dict | None, *, log=print) -> None:
    rd = Path(b["run_dir"])
    with open(rd / "results.jsonl", "a", encoding="utf-8") as f:
        for r in fresh:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if fresh and server:
        m = json.loads((rd / "run.json").read_text())
        m["model_info"] = server
        m["model"] = m.get("model") or fresh[0].get("model")
        (rd / "run.json").write_text(json.dumps(m, indent=1))
    log(f"  g0: {len(fresh)} result(s) into {rd}")


# ---- tally

def _norm(t: str) -> str:
    return re.sub(r"[\s_]+", " ", str(t).strip().lower().strip(".;:,"))


def outputs(rd: Path) -> dict[str, dict[str, dict]]:
    """{step: {key: output}}, the last ok result per job."""
    from .batch_runner import parse_json
    out = defaultdict(dict)
    for r in _jsonl(rd / "results.jsonl"):
        if not r.get("ok"):
            continue
        step, _, key = r["id"].removeprefix("g0-").partition("-")
        o = r.get("output")
        if isinstance(o, str):
            try:
                o = parse_json(o)
            except Exception:
                continue
        if isinstance(o, dict):
            out[step][key] = o
    return out


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            if k != "passages":
                yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def scrub(o, grams: set, title: str = "", n: int = COPY_WORDS):
    """o with every string that copies a run of n+ words from the paper blanked; and how many were."""
    hit = 0

    def walk(x):
        nonlocal hit
        if isinstance(x, str):
            if len(x.split()) >= n and copyfix.longest_copy(x, grams, title=title)[0] >= n:
                hit += 1
                return "[removed: copied from the paper]"
            return x
        if isinstance(x, dict):
            return {k: (v if k == "passages" else walk(v)) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    return walk(o), hit


def _count(papers: dict[str, list[str]], top: int) -> list[list]:
    """[term, papers] for terms by the number of papers that give them."""
    c = Counter()
    for terms in papers.values():
        c.update(set(_norm(t) for t in terms if t and _norm(t)))
    return [[t, k] for t, k in c.most_common(top)]


def tally(lib, rd: Path, *, top: int = 80) -> dict:
    out = outputs(rd)
    sv, inv = out.get("survey", {}), out.get("inventory", {})
    clean, removed = {}, 0
    for key, o in inv.items():
        it = lib.get(key)
        grams = copyfix.ngrams(it.text()) if it.has_text else set()
        clean[key], h = scrub(o, grams, it.title)
        removed += h
    sources = Counter(j.get("source") for j in _jsonl(rd / "jobs.jsonl") if j.get("step") == "survey")
    bundle = {
        "run": rd.name, "papers_surveyed": len(sv), "papers_inventoried": len(clean),
        "survey_sources": dict(sources), "copied_strings_removed": removed,
        "survey": {
            "paper_kind": _count({k: [o.get("paper_kind", "")] for k, o in sv.items()}, top),
            "studies": _count({k: o.get("studies", []) for k, o in sv.items()}, top),
            "approach": _count({k: o.get("approach", []) for k, o in sv.items()}, top),
            "uses_kind": _count({k: [u.get("kind", "") for u in o.get("uses", [])] for k, o in sv.items()}, top),
            "uses_name": _count({k: [u.get("name", "") for u in o.get("uses", [])] for k, o in sv.items()}, top),
            "conditions": _count({k: o.get("conditions", []) for k, o in sv.items()}, top)},
        "inventory": {
            "paper_kind": _count({k: [o.get("paper_kind", "")] for k, o in clean.items()}, top),
            "experiment_produces": _count({k: [e.get("produces", "") for e in o.get("experiments", [])]
                                           for k, o in clean.items()}, top),
            "uses_kind": _count({k: [u.get("kind", "") for e in o.get("experiments", []) for u in e.get("uses", [])]
                                 for k, o in clean.items()}, top),
            "key_facts": _count({k: [f.get("fact", "") for e in o.get("experiments", [])
                                     for f in e.get("key_facts", [])] for k, o in clean.items()}, top),
            "independence": _count({k: [x.get("shared", "") for x in o.get("independence", [])]
                                    for k, o in clean.items()}, top),
            "conditions": _count({k: [x.get("dimension", "") for x in o.get("conditions", [])]
                                  for k, o in clean.items()}, top),
            "claim_basis": _count({k: [c.get("basis", "") for c in o.get("claims", [])] for k, o in clean.items()},
                                  top),
            "reliability_checks": _count({k: o.get("reliability_checks", []) for k, o in clean.items()}, top),
            "missing": _count({k: o.get("missing", []) for k, o in clean.items()}, top),
            "terms": _count({k: [t.get("term", "") for t in o.get("terms", [])] for k, o in clean.items()}, top)}}
    (rd / "bundle.json").write_text(json.dumps(bundle, indent=1, ensure_ascii=False))
    _write_jsonl(rd / "inventories.clean.jsonl", [{"key": k, **v} for k, v in sorted(clean.items())])
    return bundle


# ---- synthesis and review (Claude and Gemini: no licensed text)

def _strip_passages(o):
    if isinstance(o, dict):
        return {k: _strip_passages(v) for k, v in o.items() if k != "passages"}
    if isinstance(o, list):
        return [_strip_passages(v) for v in o]
    return o


def evidence_text(rd: Path, *, max_chars: int = 400_000) -> str:
    """The bundle and the cleaned inventories, without passage ids, keyed by an anonymous number."""
    b = json.loads((rd / "bundle.json").read_text())
    inv = _jsonl(rd / "inventories.clean.jsonl")
    parts = ["## Tallies (term, number of papers)", json.dumps(b, ensure_ascii=False), "",
             "## Inventories, one per paper (written by a local model from the paper's text)"]
    used = sum(len(p) for p in parts)
    for i, r in enumerate(inv, 1):
        r = _strip_passages({k: v for k, v in r.items() if k != "key"})
        s = f"### Paper {i}\n{json.dumps(r, ensure_ascii=False)}"
        if used + len(s) > max_chars:
            parts.append(f"(… {len(inv) - i + 1} more inventories left out for length)")
            break
        parts.append(s)
        used += len(s)
    return "\n".join(parts)


def open_papers(lib, rd: Path, n: int, chars: int) -> list:
    """Up to n open papers from the sample, spread across strata, with their text (open: may go out)."""
    rows = [r for r in _jsonl(rd / "sample.jsonl") if r.get("open")]
    by = defaultdict(list)
    for r in rows:
        by[r["stratum"]].append(r["key"])
    order, i = [], 0
    while len(order) < min(n, len(rows)):
        for s in sorted(by):
            if i < len(by[s]) and len(order) < n:
                order.append(by[s][i])
        i += 1
    items = [lib.get(k) for k in order]
    derived.outbound(lib, items)
    return [(it, it.text()[:chars]) for it in items]


SCHEMA_OUT = {
    "type": "object", "additionalProperties": False,
    "required": ["field_description", "evidence_kinds", "experiment", "classifying_facts", "proposed_rules",
                 "conditions", "topics", "g1_prompt", "notes"],
    "properties": {
        "field_description": {"type": "string"},
        "evidence_kinds": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "definition", "recognise_by"],
            "properties": {"name": {"type": "string"}, "definition": {"type": "string"},
                           "recognise_by": {"type": "string"}}}},
        "experiment": {"type": "object", "additionalProperties": False,
                       "required": ["definition", "index_fields"],
                       "properties": {"definition": {"type": "string"}, "index_fields": {"type": "array", "items": {
                           "type": "object", "additionalProperties": False,
                           "required": ["name", "definition", "form", "why"],
                           "properties": {"name": {"type": "string"}, "definition": {"type": "string"},
                                          "form": {"type": "string"}, "why": {"type": "string"}}}}}},
        "classifying_facts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "definition", "why"],
            "properties": {"name": {"type": "string"}, "definition": {"type": "string"}, "why": {"type": "string"}}}},
        "proposed_rules": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["rule", "basis", "needs_approval"],
            "properties": {"rule": {"type": "string"}, "basis": {"type": "string"},
                           "needs_approval": {"type": "boolean"}}}},
        "conditions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["dimension", "definition", "examples"],
            "properties": {"dimension": {"type": "string"}, "definition": {"type": "string"},
                           "examples": {"type": "array", "items": {"type": "string"}}}}},
        "topics": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "description", "why", "share"],
            "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                           "why": {"type": "string"}, "share": {"type": "string"}}}},
        "g1_prompt": {"type": "string"},
        "notes": {"type": "string"}}}

SYNTH_SYSTEM = (
    "You design the domain schema of a claim graph for a body of scientific literature you have not been "
    "told anything about. The core of the graph is fixed (below); everything specific to the field is for "
    "you to work out from the evidence: a survey of every paper, inventories of a stratified sample written "
    "by a local model, and the text of a few open-access papers. Derive, don't assume: every element should "
    "be traceable to the evidence, and say how widely it occurs. Produce:\n"
    "- field_description: what this literature is, in a paragraph.\n"
    "- evidence_kinds: the kinds of evidence its shown claims rest on, each defined and with how to "
    "recognise it in a paper.\n"
    "- experiment: what an experiment is in this field, and the fields of a short index of each paper's "
    "experiments (the anchors that later, topic-specific passes attach detail to); for each field its "
    "definition, form (as written / a closed list / a number) and why it is needed.\n"
    "- classifying_facts: the few facts about an experiment that decide which evidence kind it is or how "
    "far its results can be compared, and why.\n"
    "- proposed_rules: rules that turn those facts into classes (e.g. thresholds). Each is a proposal for a "
    "person to approve: say what in the evidence it is based on.\n"
    "- conditions: the vocabulary of conditions that limit where claims hold.\n"
    "- topics: 15-25 candidate topics: aspects that many papers report in detail (a method, a kind of "
    "experiment, a phenomenon, a kind of data), which may cut across sub-fields, with roughly what share "
    "of papers they cover.\n"
    "- g1_prompt: the complete system prompt for the general extraction pass, for a mid-sized local model "
    "reading a paper section by section: mentions with the core fields and the domain schema's fields, and "
    "the experiment index. Concrete, with the definitions it needs.\n"
    "- notes: open questions, weak spots in the evidence, and what you would check next.")

REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdict", "issues", "missing"],
    "properties": {
        "verdict": {"type": "string", "enum": ["usable", "usable_with_changes", "not_usable"]},
        "issues": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["where", "problem", "suggestion", "severity"],
            "properties": {"where": {"type": "string"}, "problem": {"type": "string"},
                           "suggestion": {"type": "string"},
                           "severity": {"type": "string", "enum": ["major", "minor"]}}}},
        "missing": {"type": "array", "items": {"type": "string"}}}}

REVIEW_SYSTEM = (
    "You review a draft domain schema for a claim graph, written by another model from the evidence "
    "given. The core is fixed. Check each element of the draft against the evidence: is it supported, is "
    "it defined clearly enough for a mid-sized model to apply, does it serve the purpose, does it miss "
    "something the evidence shows matters (look especially at the inventories' key facts, independence "
    "and reliability checks), is any proposed rule unjustified? Check that the G1 prompt is complete and "
    "consistent with the schema. Be specific: say where, what the problem is and what to do.")


def _prompt(rd: Path, lib=None, *, n_open: int = 10, open_chars: int = 30_000, extra: str = "") -> str:
    parts = ["# The fixed core", CORE, "# The purpose", PURPOSE, "# Evidence", evidence_text(rd)]
    if lib is not None and n_open:
        parts.append("## Open-access papers (full text, truncated)")
        for it, text in open_papers(lib, rd, n_open, open_chars):
            parts.append(f"### {it.title} ({it.year})\n{text}")
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def _claude(model: str, system: str, prompt: str, schema: dict, timeout: int = 3600) -> dict:
    env = derived.run_claude(derived.claude_code_args(model, schema, system=system), prompt, timeout=timeout)
    out = env.get("structured_output") or env.get("result")
    if isinstance(out, str):
        from .batch_runner import parse_json
        out = parse_json(out)
    if not isinstance(out, dict):
        raise RuntimeError(f"{model}: no structured output")
    return out


def _gemini(system: str, prompt: str, schema: dict, model: str = GEMINI, timeout: int = 3600) -> dict:
    """agy takes the prompt in argv; a long prompt goes in a file it is told to read."""
    import shutil
    import subprocess
    import tempfile
    if not shutil.which("agy"):
        raise RuntimeError("the agy CLI is not installed")
    with tempfile.TemporaryDirectory(prefix="litnav-agy-") as td:
        (Path(td) / "input.md").write_text(prompt, encoding="utf-8")
        cmd = ["agy", "-p", f"{system}\n\nThe material to work from is in the file input.md in this folder. "
               "Read all of it.", "--output-format", "json", "--json-schema", json.dumps(schema),
               "--model", model, "--sandbox", "--disable-slash-commands"]
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=td, timeout=timeout, stdin=subprocess.DEVNULL)
    try:
        out = json.loads(p.stdout).get("structured_output")
    except json.JSONDecodeError:
        out = None
    if not isinstance(out, dict):
        raise RuntimeError(f"agy: no structured output: {(p.stderr or p.stdout)[:300]}")
    return out


def synthesize(lib, rd: Path, *, model: str = FABLE, n_open: int = 10, open_chars: int = 30_000) -> dict:
    prompt = _prompt(rd, lib, n_open=n_open, open_chars=open_chars)
    (rd / "synthesis.prompt.md").write_text(prompt, encoding="utf-8")
    out = _claude(model, SYNTH_SYSTEM, prompt, SCHEMA_OUT)
    _save_draft(rd, out, model, "draft-1")
    return out


def _save_draft(rd: Path, out: dict, model: str, name: str) -> None:
    (rd / f"{name}.json").write_text(json.dumps({"model": model, "prompt_version": PROMPT_VERSION,
                                                 "at": dt.datetime.now().isoformat(timespec="seconds"),
                                                 "schema": out}, indent=1, ensure_ascii=False))


def drafts(rd: Path) -> list[Path]:
    return sorted(rd.glob("draft-*.json"), key=lambda p: int(p.stem.split("-")[1]))


def review(lib, rd: Path, *, who=("opus", "gemini")) -> dict:
    d = drafts(rd)[-1]
    draft = json.loads(d.read_text())["schema"]
    prompt = _prompt(rd, extra="# The draft to review\n" + json.dumps(draft, indent=1, ensure_ascii=False))
    out = {}
    for w in who:
        out[w] = (_claude(OPUS, REVIEW_SYSTEM, prompt, REVIEW_SCHEMA) if w == "opus"
                  else _gemini(REVIEW_SYSTEM, prompt, REVIEW_SCHEMA))
    (rd / f"review-{d.stem}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    return out


def revise(lib, rd: Path, *, model: str = FABLE) -> dict:
    d = drafts(rd)[-1]
    rv = rd / f"review-{d.stem}.json"
    if not rv.is_file():
        raise FileNotFoundError(f"no review of {d.stem} yet: `litnav g0 review`")
    extra = ("# Your draft\n" + json.dumps(json.loads(d.read_text())["schema"], indent=1, ensure_ascii=False)
             + "\n\n# Two reviews of it\n" + rv.read_text()
             + "\n\nRevise the draft: act on each issue you agree with, and say in notes which you did not "
               "act on and why.")
    out = _claude(model, SYNTH_SYSTEM, _prompt(rd, extra=extra), SCHEMA_OUT)
    _save_draft(rd, out, model, f"draft-{int(d.stem.split('-')[1]) + 1}")
    return out


def status(rd: Path) -> dict:
    js = _jsonl(rd / "jobs.jsonl")
    out = outputs(rd)
    return {"run": str(rd), "sample": len(_jsonl(rd / "sample.jsonl")),
            "jobs": dict(Counter(j["step"] for j in js)),
            "results": {k: len(v) for k, v in out.items()},
            "bundle": (rd / "bundle.json").is_file(), "drafts": [p.name for p in drafts(rd)],
            "reviews": sorted(p.name for p in rd.glob("review-*.json"))}
