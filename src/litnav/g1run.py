"""G1 with an induced prompt: the general extraction pass run with the G1 prompt of a G0 draft.

The draft's prompt (schema["g1_prompt"]) is used verbatim as the system prompt. The calls follow the v7
pattern the node runner already supports: per paper, one index call over the methods passages, then one
call per group of sections that waits for it ("after") and gets its index in place of claims.RUNS_SLOT
(copyfix.fill). Statements that copy the paper are rewritten in the same run (copyfix follow-ups).

Run folders: derived/g1/<stamp>[-label]/ with run.json (the draft it came from, its hash, the model),
prompt.md, jobs.jsonl and results.jsonl. `records` turns results into one record list per paper.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path

from . import claims
from . import sharing as S

INDEX_NOTE = (
    "\n\n=== THIS CALL ===\nThis call reads the paper's methods passages only. Return the experiment index "
    "for the whole paper: {\"experiments\": [...]}, every experiment these passages describe, in the format "
    "above. Each passage is labelled with its id in square brackets.")
SECTION_NOTE = (
    "\n\n=== THIS CALL ===\nThe experiment index built from this paper's methods is given below. Return "
    "{\"experiments\": [...], \"mentions\": [...]} in the format above: in experiments, only experiments "
    "missing from that index; in mentions, every mention in these passages. Each passage is labelled with "
    "its id in square brackets: give each mention's passage id in the field \"chunk\".")


def draft_prompt(draft: Path) -> tuple[str, dict]:
    d = json.loads(Path(draft).read_text())
    p = (d.get("schema") or {}).get("g1_prompt") or ""
    if len(p) < 500:
        raise ValueError(f"{draft}: no G1 prompt in this draft")
    return p, {"draft": str(draft), "drafted_by": d.get("model"), "sha256": hashlib.sha256(p.encode()).hexdigest()[:16]}


def index_schema() -> dict:
    return {"type": "object", "properties": {"experiments": {"type": "array", "items": {"type": "object"}}},
            "required": ["experiments"]}


def section_schema(ids: list[str]) -> dict:
    m = {"type": "object", "properties": {"statement": {"type": "string"},
                                          "chunk": {"type": "string", "enum": ids}},
         "required": ["statement", "chunk"]}
    return {"type": "object", "properties": {"experiments": {"type": "array", "items": {"type": "object"}},
                                             "mentions": {"type": "array", "maxItems": claims.MAX_ITEMS,
                                                          "items": m}},
            "required": ["experiments", "mentions"]}


def jobs(lib, it, prompt: str, *, prefix: str = "g1") -> list[dict]:
    """The index call, then one call per group of sections waiting for it."""
    rows = claims.chunks(lib, it)
    if not rows:
        return []
    ctx = claims.context(it, rows)
    g = claims.methods_rows(rows)
    ix = {"id": f"{prefix}-{it.key}--runs", "kind": "eval", "task": "g1", "key": it.key, "role": "runs",
          "schema": index_schema(), "passages": [r["id"] for r in g],
          "chars_sent": sum(len(r["text"]) for r in g),
          "messages": [{"role": "system", "content": prompt + INDEX_NOTE},
                       {"role": "user", "content": f"{ctx}\n\nPassages:\n{claims._body(g)}\n\nReply with JSON only."}]}
    out = [ix]
    for n, grp in enumerate(claims.groups(rows), 1):
        ids = [r["id"] for r in grp]
        out.append({"id": f"{prefix}-{it.key}--g{n:02d}", "kind": "eval", "task": "g1", "key": it.key, "group": n,
                    "schema": section_schema(ids), "passages": ids,
                    "chars_sent": sum(len(r["text"]) for r in grp),
                    "messages": [{"role": "system", "content": prompt + SECTION_NOTE},
                                 {"role": "user", "content": f"{ctx}\n\nExperiment index (from the methods):\n"
                                  f"{claims.RUNS_SLOT}\n\nPassages:\n{claims._body(grp)}\n\nReply with JSON only."}],
                    "followup": "mentions", "title": it.title or "", "after": ix["id"]})
    return out


def papers(lib, keys: list[str] | None = None) -> list:
    """Papers the local model may read (never confidential), with text; `keys` to restrict."""
    its = [it for it in lib.items(S.LOCAL_MODEL) if it.has_text] if not keys else [lib.get(k) for k in keys]
    return [it for it in its if it.allowed(S.LOCAL_MODEL)]


def new_run(lib, draft: Path, *, keys: list[str] | None = None, label: str = "") -> Path:
    prompt, meta = draft_prompt(draft)
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    rd = lib.root / "derived" / "g1" / (stamp + (f"-{label}" if label else ""))
    rd.mkdir(parents=True, exist_ok=False)
    js = [j for it in papers(lib, keys) for j in jobs(lib, it, prompt)]
    (rd / "prompt.md").write_text(prompt, encoding="utf-8")
    with open(rd / "jobs.jsonl", "w", encoding="utf-8") as f:
        for j in js:
            f.write(json.dumps(j, ensure_ascii=False) + "\n")
    (rd / "run.json").write_text(json.dumps({
        "pass": "G1", "prompt_from": meta, "created": dt.datetime.now().isoformat(timespec="seconds"),
        "papers": len({j["key"] for j in js}), "jobs": len(js), "chars_per_call": claims.GROUP_CHARS}, indent=1))
    return rd


def push(lib, rd: Path, *, submit: bool = True, log=print) -> dict:
    from . import spool
    js = [json.loads(l) for l in (rd / "jobs.jsonl").read_text().splitlines() if l.strip()]
    m = json.loads((rd / "run.json").read_text())
    m["pushed"] = dt.datetime.now().isoformat(timespec="seconds")
    (rd / "run.json").write_text(json.dumps(m, indent=1))
    return spool.push(lib, js, kind="g1", meta={"run_dir": str(rd)}, submit=submit, log=log)


def import_batch(lib, b: dict, jobs: dict, fresh: list, server: dict | None, *, log=print) -> None:
    rd = Path(b["run_dir"])
    with open(rd / "results.jsonl", "a", encoding="utf-8") as f:
        for r in fresh:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if fresh and server:
        m = json.loads((rd / "run.json").read_text())
        m["model_info"], m["model"] = server, m.get("model") or fresh[0].get("model")
        (rd / "run.json").write_text(json.dumps(m, indent=1))
    log(f"  g1: {len(fresh)} result(s) into {rd}")


def _results(rd: Path) -> dict:
    p = rd / "results.jsonl"
    out = {}
    for l in (p.read_text().splitlines() if p.is_file() else []):
        if l.strip():
            r = json.loads(l)
            out[r["id"]] = r
    return out


def records(rd: Path) -> dict[str, dict]:
    """Per paper: {experiments, mentions, failed}. Section calls' extra experiments are appended to the
    index; a mention's statement takes its accepted rewrite (the -r reply, kept only where the -c check
    passed, as in paper-eval)."""
    from . import copyfix
    res = _results(rd)
    js = [json.loads(l) for l in (rd / "jobs.jsonl").read_text().splitlines() if l.strip()]
    out: dict[str, dict] = {}
    for j in js:
        p = out.setdefault(j["key"], {"experiments": [], "mentions": [], "failed": []})
        r = res.get(j["id"])
        o = r.get("output") if r and r.get("ok") else None
        if not isinstance(o, dict):
            p["failed"].append(j["id"])
            continue
        p["experiments"] += [e for e in o.get("experiments") or [] if isinstance(e, dict)]
        if j.get("role") == "runs":
            continue
        ms = [m for m in o.get("mentions") or [] if isinstance(m, dict)]
        again, check = res.get(f"{j['id']}-r"), res.get(f"{j['id']}-c")
        if again and again.get("ok") and isinstance(again.get("output"), dict):
            o2, _ = copyfix.merge_rewrites({"mentions": ms}, again["output"], again.get("rewrite") or [],
                                           check.get("output") if check and check.get("ok") else None,
                                           (check or {}).get("meaning_check"))
            ms = o2["mentions"]
        p["mentions"] += [{**m, "_job": j["id"]} for m in ms]
    return out


def report(rd: Path) -> dict:
    recs = records(rd)
    ms = [m for p in recs.values() for m in p["mentions"]]
    keys = Counter(k for m in ms for k in m if not k.startswith("_"))
    return {"papers": len(recs), "papers_with_failures": sum(1 for p in recs.values() if p["failed"]),
            "failed_calls": sum(len(p["failed"]) for p in recs.values()),
            "mentions": len(ms), "mentions_per_paper": round(len(ms) / max(1, len(recs)), 1),
            "experiments": sum(len(p["experiments"]) for p in recs.values()),
            "fields": dict(keys.most_common(40))}
