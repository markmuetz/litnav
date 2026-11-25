"""Topic passes (framework v2): T0 builds a topic's template from its papers; T1 fills it for each paper.

Routing comes from the topics pass (topics.py): a paper is routed to a topic when one of its aspects falls in
the topic's clusters. T0: an inventory of what each routed paper reports about the topic (local model or
Haiku), a tally of the kinds of detail (merged by consensus clustering, as for topics), a draft template by
the drafter (Opus), review by Fable and Gemini, revision by the drafter. Every field lists the inventories it
rests on (mapped back to papers by code), and numbers in a field's definition or allowed values that appear
nowhere in the evidence are flagged (Mark, 25 Nov 2026: rules drawn from the literature, traceable). T1: one
call per routed paper; the template is the reply schema; each field is a value as written, or not_stated,
with the passage it comes from.

Run folders: derived/templates/<stamp>-<topic>/ (routing.json, jobs.jsonl, results.jsonl, tally.json,
draft-N.json, review-draft-N.json, t1/)."""
from __future__ import annotations

import datetime as dt
import json
import random
import re
from pathlib import Path

from . import sharing as S

PROMPT_VERSION = 1
CHARS = 200_000
MAX_INVENTORIES = 40       # papers inventoried per topic for T0 (a cost cap, not a property of the topic)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48]


def find_topic(topics_rd: Path, name: str) -> dict:
    t = json.loads((Path(topics_rd) / "topics.json").read_text())["topics"]
    hits = [x for x in t if x["name"].lower() == name.lower()] or [x for x in t if name.lower() in x["name"].lower()]
    if len(hits) != 1:
        raise ValueError(f"{len(hits)} topics match {name!r}: {[x['name'] for x in hits][:5]}")
    return hits[0]


def route(lib, topics_rd: Path, name: str, *, open_only: bool = True) -> tuple[Path, dict]:
    """A run folder for the topic, with routing.json: the papers whose aspects fall in its clusters, and
    those aspects (the router is generous: one aspect is enough)."""
    from .topics import aspects
    topic = find_topic(topics_rd, name)
    cl = {c["id"]: c for c in json.loads((Path(topics_rd) / "clusters.json").read_text())["clusters"]}
    members = {k for i in topic["clusters"] for k in cl[i]["keys"]}
    samples = {s for i in topic["clusters"] for s in cl[i]["sample"]}
    asp = [a for a in aspects(Path(topics_rd)) if a["key"] in members and a["aspect"] in samples]
    keys = sorted(k for k in members if (lambda it: it is not None and it.has_text and it.allowed(
        S.SEND_OUT if open_only else S.LOCAL_MODEL))(lib.get(k)))
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    rd = lib.root / "derived" / "templates" / f"{stamp}-{slug(topic['name'])}"
    rd.mkdir(parents=True, exist_ok=True)
    r = {"topic": topic["name"], "description": topic["description"], "kind": topic["kind"],
         "topics_run": Path(topics_rd).name, "open_only": open_only, "papers": keys,
         "aspects": {k: [a["aspect"] for a in asp if a["key"] == k] for k in keys}}
    (rd / "routing.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
    return rd, r


# ---- T0: inventories

INV_SYSTEM = """You read one scientific paper for one topic (given below) and list every kind of detail the paper
reports about that topic: the particulars a reader would need to compare this paper's work on the topic with
another paper's (settings, definitions, thresholds, procedures, choices, data used, values). For each give:
- detail: what kind of detail it is, a short noun phrase in your own words (e.g. "the threshold used to ...");
- value_given: whether the paper gives its value;
- example: the value as the paper gives it, in at most a few words ("" if not given);
- sections: the titles of the sections where it is reported.
Only what the paper reports. Do not copy sentences. Reply with JSON only."""

INV_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["reports_topic", "details"], "properties": {
    "reports_topic": {"type": "boolean"},
    "details": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["detail", "value_given", "example", "sections"],
                "properties": {"detail": {"type": "string"}, "value_given": {"type": "boolean"},
                               "example": {"type": "string"},
                               "sections": {"type": "array", "items": {"type": "string"}}}}}}}


def _paper(lib, it, chars=CHARS, ids=False):
    from .papereval import chunks_for
    ps = chunks_for(lib, it, chars)
    body = "\n\n".join((f"[{p['id']}] " if ids else "") + f"({p['section']})\n{p['text']}" for p in ps)
    return body, [p["id"] for p in ps]


def inventory_jobs(lib, rd: Path, *, n: int = MAX_INVENTORIES, seed: int = 1) -> list[dict]:
    r = json.loads((rd / "routing.json").read_text())
    keys = list(r["papers"])
    random.Random(seed).shuffle(keys)
    keys = sorted(keys[:n])
    js = []
    for k in keys:
        it = lib.get(k)
        body, _ = _paper(lib, it)
        js.append({"id": f"t0inv-{k}", "kind": "t0", "step": "inventory", "key": k, "schema": INV_SCHEMA,
                   "messages": [{"role": "system", "content": INV_SYSTEM},
                                {"role": "user", "content": f"Topic: {r['topic']}: {r['description']}\n\n"
                                                            f"Paper: {it.title} ({it.year})\n\n{body}\n\nReply with JSON only."}]})
    (rd / "jobs.jsonl").write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in js))
    return js


def inventories(rd: Path) -> list[dict]:
    out = []
    p = rd / "results.jsonl"
    for l in (p.read_text().splitlines() if p.is_file() else []):
        x = json.loads(l) if l.strip() else {}
        if x.get("ok") and x["id"].startswith("t0inv-"):
            out.append({"key": x["id"].removeprefix("t0inv-"), **(x["output"] or {})})
    return sorted(out, key=lambda r: r["key"])


def tally(lib, rd: Path, embedder=None) -> dict:
    """Kinds of detail merged by consensus clustering of their names, with how many papers report each."""
    import numpy as np
    from .topics import consensus
    if embedder is None:
        from .commands.searching import embedder_for
        embedder = embedder_for(lib)
    inv = inventories(rd)
    rows = [(r["key"], d) for r in inv if r.get("reports_topic", True) for d in r.get("details", [])]
    if not rows:
        raise ValueError("no inventories yet")
    m = embedder.docs([d["detail"] for _, d in rows])
    m = m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
    lab = consensus(m)
    groups: dict[int, list] = {}
    for (k, d), l in zip(rows, lab):
        groups.setdefault(l, []).append((k, d))
    out = []
    for g in groups.values():
        keys = sorted({k for k, _ in g})
        out.append({"papers": len(keys), "with_value": len({k for k, d in g if d.get("value_given")}),
                    "names": sorted({d["detail"] for _, d in g})[:12],
                    "examples": [d["example"] for _, d in g if d.get("example")][:10]})
    out.sort(key=lambda x: -x["papers"])
    t = {"papers_inventoried": len(inv), "reporting_topic": sum(bool(r.get("reports_topic", True)) for r in inv),
         "kinds": out}
    (rd / "tally.json").write_text(json.dumps(t, ensure_ascii=False, indent=1))
    return t


# ---- T0: draft, review, revise

TEMPLATE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["fields", "other_details", "notes"],
    "properties": {
        "fields": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                   "required": ["name", "definition", "applies_to", "form", "units", "allowed_values", "where_found",
                                "inventories"],
                   "properties": {"name": {"type": "string"}, "definition": {"type": "string"},
                                  "applies_to": {"type": "string", "enum": ["paper", "experiment"]},
                                  "form": {"type": "string", "enum": ["as_written", "closed_list", "number", "yes_no"]},
                                  "units": {"type": "string"},
                                  "allowed_values": {"type": "array", "items": {"type": "string"}},
                                  "where_found": {"type": "string"},
                                  "inventories": {"type": "array", "items": {"type": "integer"}}}}},
        "other_details": {"type": "string"},
        "notes": {"type": "string"}}}

DRAFT_SYSTEM = """You design the extraction template for one topic of a claim graph built from scientific papers:
the fields a later pass fills for each paper that reports the topic, so that papers' work on the topic can be
compared. Work only from the evidence given (a tally of the kinds of detail papers report, with how many
papers report each, and per-paper inventories numbered Paper N); do not add what you know of the field. For
each field give: name (snake_case); definition (precise enough for a mid-sized model to apply); applies_to
(paper, or experiment: one value per experiment the paper describes); form (as_written: the paper's own words
or numbers; closed_list; number; yes_no); units (if a number); allowed_values (for a closed list, taken from
the evidence); where_found (which sections usually give it); inventories (the numbers of the inventories that
report it). Prefer as_written over closed lists unless the evidence shows a small fixed set. Cover what many
papers report, and what few report but matters for comparing them. Use only numbers and criteria that appear
in the evidence. Also give other_details (what to do with details no field covers) and notes (weak spots).
Reply with JSON only."""

REVIEW_SYSTEM = """You review a draft extraction template for one topic, written by another model from the evidence
given. Check each field against the evidence: is it reported by the papers, is it defined clearly enough for
a mid-sized model to apply, are closed lists justified by the evidence, is anything the papers report and that
matters for comparing them missing, do any two fields overlap, does any field rest on knowledge not in the
evidence? Be specific: say where, what the problem is and what to do."""


def evidence(rd: Path) -> str:
    r = json.loads((rd / "routing.json").read_text())
    t = json.loads((rd / "tally.json").read_text())
    inv = inventories(rd)
    (rd / "inventory_index.json").write_text(json.dumps({str(i): x["key"] for i, x in enumerate(inv, 1)}))
    parts = [f"# Topic\n{r['topic']}: {r['description']}",
             f"# Tally ({t['reporting_topic']} of {t['papers_inventoried']} inventoried papers report the topic)",
             json.dumps(t["kinds"], ensure_ascii=False), "# Inventories"]
    parts += [f"### Paper {i}\n" + json.dumps({k: v for k, v in x.items() if k != "key"}, ensure_ascii=False)
              for i, x in enumerate(inv, 1)]
    return "\n".join(parts)


def _drafts(rd: Path) -> list[Path]:
    return sorted(rd.glob("draft-[0-9]*.json"), key=lambda p: int(p.stem.split("-")[1]))


def _save(rd: Path, out: dict, model: str, name: str, ev: str) -> None:
    from .g0 import check_rules
    rules = {"proposed_rules": [{"rule": " ".join([f["definition"], " ".join(f.get("allowed_values") or [])]),
                                 "inventories": f.get("inventories")} for f in out.get("fields", [])]}
    chk = check_rules(rd, rules, ev)
    for f, c in zip(out.get("fields", []), chk):
        f["papers"], f["n_papers"], f["numbers_not_in_evidence"] = c["papers"], c["n_papers"], c["numbers_not_in_evidence"]
    (rd / f"{name}.json").write_text(json.dumps({"model": model, "prompt_version": PROMPT_VERSION,
                                                 "at": dt.datetime.now().isoformat(timespec="seconds"),
                                                 "template": out}, ensure_ascii=False, indent=1))


def draft(rd: Path) -> dict:
    from .g0 import DRAFTER, _claude
    ev = evidence(rd)
    out = _claude(DRAFTER, DRAFT_SYSTEM, ev, TEMPLATE_SCHEMA)
    _save(rd, out, DRAFTER, "draft-1", ev)
    return out


def review(rd: Path) -> dict:
    from .g0 import FABLE, REVIEW_SCHEMA, REVIEWERS, _claude, _gemini
    d = _drafts(rd)[-1]
    prompt = evidence(rd) + "\n\n# The draft to review\n" + json.dumps(json.loads(d.read_text())["template"],
                                                                        ensure_ascii=False, indent=1)
    out = {w: (_gemini(REVIEW_SYSTEM, prompt, REVIEW_SCHEMA) if w == "gemini"
               else _claude(FABLE, REVIEW_SYSTEM, prompt, REVIEW_SCHEMA)) for w in REVIEWERS}
    (rd / f"review-{d.stem}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def revise(rd: Path) -> dict:
    from .g0 import DRAFTER, _claude
    d = _drafts(rd)[-1]
    ev = evidence(rd)
    extra = ("\n\n# Your draft\n" + json.dumps(json.loads(d.read_text())["template"], ensure_ascii=False, indent=1)
             + "\n\n# Two reviews of it\n" + (rd / f"review-{d.stem}.json").read_text()
             + "\n\nRevise the draft: act on each issue you agree with; say in notes which you did not act on and why.")
    out = _claude(DRAFTER, DRAFT_SYSTEM, ev + extra, TEMPLATE_SCHEMA)
    _save(rd, out, DRAFTER, f"draft-{int(d.stem.split('-')[1]) + 1}", ev)
    return out


# ---- T1: fill the template

T1_SYSTEM = """You fill an extraction template for one topic from one scientific paper. For each field, give the
value as the paper states it (as written; for a closed list, one of the allowed values) and the id of the
passage it comes from; give "not_stated" if the paper does not state it (do not guess or infer). Fields that
apply to experiments are filled once per experiment the paper describes for this topic, each named as the
paper names it. Say first whether the paper reports this topic at all. Reply with JSON only."""


def t1_schema(template: dict, ids: list[str]) -> dict:
    pid = {"type": "string", "enum": ids + [""]}
    val = lambda: {"type": "object", "additionalProperties": False, "required": ["value", "passage"],
                   "properties": {"value": {"type": "string"}, "passage": pid}}
    pf = [f["name"] for f in template["fields"] if f["applies_to"] == "paper"]
    ef = [f["name"] for f in template["fields"] if f["applies_to"] == "experiment"]
    return {"type": "object", "additionalProperties": False, "required": ["reports_topic", "paper", "experiments", "other"],
            "properties": {
                "reports_topic": {"type": "boolean"},
                "paper": {"type": "object", "additionalProperties": False, "required": pf,
                          "properties": {n: val() for n in pf}},
                "experiments": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["name"] + ef,
                    "properties": {"name": {"type": "string"}, **{n: val() for n in ef}}}},
                "other": {"type": "string"}}}


def t1_jobs(lib, rd: Path, *, keys: list[str] | None = None) -> Path:
    d = _drafts(rd)[-1]
    tpl = json.loads(d.read_text())["template"]
    r = json.loads((rd / "routing.json").read_text())
    out = rd / "t1"
    out.mkdir(exist_ok=True)
    fields = "\n".join(f"- {f['name']} ({f['applies_to']}, {f['form']}{', ' + f['units'] if f['units'] else ''}): "
                       f"{f['definition']}" + (f" Allowed: {', '.join(f['allowed_values'])}." if f['allowed_values'] else "")
                       for f in tpl["fields"])
    js = []
    for k in keys or r["papers"]:
        it = lib.get(k)
        body, ids = _paper(lib, it, ids=True)
        js.append({"id": f"t1-{k}", "kind": "t1", "step": "t1", "key": k, "schema": t1_schema(tpl, ids),
                   "messages": [{"role": "system", "content": T1_SYSTEM},
                                {"role": "user", "content": f"Topic: {r['topic']}: {r['description']}\n\nTemplate "
                                 f"({d.stem}):\n{fields}\n\nOther details: {tpl['other_details']}\n\n"
                                 f"Paper: {it.title} ({it.year})\n\nPassages:\n{body}\n\nReply with JSON only."}]})
    (out / "jobs.jsonl").write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in js))
    (out / "run.json").write_text(json.dumps({"pass": "T1", "topic": r["topic"], "template": d.name,
                                              "papers": len(js)}, indent=1))
    return out


def t1_summary(rd: Path) -> dict:
    """How often each field is filled (not not_stated) across the papers that report the topic."""
    res = [json.loads(l) for l in (rd / "t1" / "results.jsonl").read_text().splitlines() if l.strip()]
    ok = [r["output"] for r in res if r.get("ok")]
    rep = [o for o in ok if o.get("reports_topic")]
    filled: dict[str, int] = {}
    for o in rep:
        for n, v in (o.get("paper") or {}).items():
            filled[n] = filled.get(n, 0) + (v.get("value", "not_stated") != "not_stated")
        for e in o.get("experiments") or []:
            for n, v in e.items():
                if isinstance(v, dict):
                    filled[n] = filled.get(n, 0) + (v.get("value", "not_stated") != "not_stated")
    s = {"papers": len(ok), "reporting_topic": len(rep),
         "experiments": sum(len(o.get("experiments") or []) for o in rep), "filled": dict(sorted(filled.items(), key=lambda x: -x[1]))}
    (rd / "t1" / "summary.json").write_text(json.dumps(s, indent=1))
    return s
