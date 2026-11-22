"""Evaluate the local model's claim checking: `litnav eval-model run|report`.

The eval set (JSONL, e.g. eval/model-claims.jsonl in the library) holds
claims about papers in the library, each with the verdict expected
(`ideal`) and the verdicts accepted (`acceptable`), and a `type` (true,
number, direction, condition, hedge, absent, bib_true, bib_false, ...).
Rows with `question` are rechecks: the claim again, with one neutral
question about it (hk27's review loop), to see whether a doubt moves the
verdict when it should and when it shouldn't.

Prompt variants:
  A  the `litnav verify` prompt as it is
  B  a stepwise prompt: split the claim into parts (fact, numbers, direction,
     conditions, strength), check each, then decide; reports hedging
     (overstated or not) and what differs, and no quote
Rechecks always use B plus the question.

Every call is stored (raw reply, parsed verdict, seconds) in
derived/model-eval/<run>/results.jsonl. That folder is git-ignored: replies
can hold short quotes of licensed papers. The report never shows quotes.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import batch_runner, localmodel

SCHEMA_B = {
    "type": "object",
    "properties": {
        "parts": {"type": "array", "items": {"type": "object", "properties": {
            "part": {"type": "string"},
            "status": {"type": "string", "enum": ["matches", "differs", "not_addressed"]}},
            "required": ["part", "status"], "additionalProperties": False}},
        "hedging": {"type": "string", "enum": ["matches", "overstated", "understated", "n/a"]},
        "verdict": {"type": "string", "enum": ["supported", "partly_supported", "contradicted",
                                               "not_found"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "passages": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
        "mismatch": {"type": "string", "description": "What differs between the claim and the "
                     "paper, in your own words, or ''"},
        "explanation": {"type": "string"},
    },
    "required": ["parts", "hedging", "verdict", "confidence", "passages", "mismatch", "explanation"],
    "additionalProperties": False,
}

SYSTEM_B = (
    "You check one claim against one academic paper for a researcher. You see the claim and "
    "numbered passages extracted from the paper's PDF (noisy: running heads, OCR errors). Use "
    "only the passages, not what you know.\n"
    "Work in steps:\n"
    "1. Split the claim into its parts: the main statement; every number with its units; every "
    "direction or sign (more/less, increase/decrease, positive/negative, which one is larger); "
    "every condition or scope (model, region, resolution, period, all/some); and how strongly "
    "it is stated (shown, suggested, proved, always).\n"
    "2. For each part, find what the passages say and mark it matches, differs, or "
    "not_addressed. A different number, unit, sign or direction differs. A claim that is "
    "wider than the paper (all cases where the paper has some; a cause the paper does not give) "
    "differs.\n"
    "3. Hedging: 'overstated' if the claim is more certain or more general than the paper "
    "(proves vs suggests, all vs some), else 'matches' (or 'understated', or 'n/a').\n"
    "4. Verdict: 'supported' only if every part matches and the hedging is not overstated. "
    "'contradicted' if the main statement, a number or a direction differs. "
    "'partly_supported' if the main statement matches but a condition differs or is not "
    "addressed, or the claim is overstated. 'not_found' if the passages do not address the "
    "main statement. Bibliographic claims (journal, volume, pages, article number, year) are "
    "decided from the header or citation line.\n"
    "In `passages` give the ids of the passages your verdict rests on. Write the explanation "
    "and mismatch in your own words; do not copy sentences. "
    "Reply with JSON only.")


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]


def build_jobs(lib, cases: list[dict], variants=("A", "B")) -> list[dict]:
    """One job per (case, variant); rechecks once, with B. The passages are
    fetched once per claim, so both variants see the same ones."""
    jobs, cache = [], {}
    for c in cases:
        it = lib.get(c["key"])
        if c["claim"] not in cache:
            cache[c["claim"]] = localmodel.verify_job(lib, it.key, c["claim"])
        base = cache[c["claim"]]
        user_a = base["messages"][1]["content"]
        body = user_a.split("\n\nPassages:\n", 1)[1].rsplit("\n\nReply with JSON only.", 1)[0]
        schema_b = localmodel.with_passage_ids(SCHEMA_B, base["passages"])
        head = f"Claim: {c['claim']}\n\nPaper: {it.title} ({it.year})\n\n"
        if c.get("question"):
            user = (head + f"Question about this claim: {c['question']}\nAnswer the question in "
                    "the explanation, then give your verdict on the claim.\n\nPassages:\n" + body
                    + "\n\nReply with JSON only.")
            jobs.append(_job(c, "R", base, SYSTEM_B, user, schema_b))
            continue
        for v in variants:
            if v == "A":
                jobs.append(_job(c, "A", base, base["messages"][0]["content"], user_a, base["schema"]))
            else:
                jobs.append(_job(c, "B", base, SYSTEM_B, head + "Passages:\n" + body
                                 + "\n\nReply with JSON only.", schema_b))
    return jobs


def _job(c, variant, base, system, user, schema):
    return {"id": f"{c['id']}-{variant}", "case": c["id"], "variant": variant, "key": base["key"],
            "passages": base["passages"], "prompt_chars": len(system) + len(user), "schema": schema,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}


def order(jobs: list[dict], seed: int = 1) -> list[dict]:
    """Random order, keeping a claim's A and B next to each other (so a time
    limit cuts whole pairs), rechecks spread through."""
    groups = defaultdict(list)
    for j in jobs:
        groups[j["case"]].append(j)
    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    out = []
    for k in keys:
        g = groups[k]
        random.Random(f"{seed}{k}").shuffle(g)       # A first half the time
        out += g
    return out


def run(lib, eval_path: Path, run_dir: Path, *, limit_minutes: float = 88, log=print) -> dict:
    cases = load(eval_path)
    url, model, kw = localmodel.endpoint(lib)
    run_dir.mkdir(parents=True, exist_ok=True)
    res_path = run_dir / "results.jsonl"
    done = {json.loads(l)["id"] for l in res_path.read_text().splitlines() if l.strip()} \
        if res_path.is_file() else set()
    t0 = time.time()
    jobs = order(build_jobs(lib, cases))
    log(f"{len(jobs)} calls ({len(done)} done before), built in {time.time() - t0:.0f} s; "
        f"model {model} at {url}; limit {limit_minutes} min")
    (run_dir / "run.json").write_text(json.dumps({
        "started": dt.datetime.now().isoformat(timespec="seconds"), "model": model, "url": url,
        "profile": lib.local_model.get("profile"), "eval": str(eval_path), "n_jobs": len(jobs),
        "limit_minutes": limit_minutes}, indent=1))
    start, n = time.time(), 0
    for j in jobs:
        if j["id"] in done:
            continue
        if time.time() - start > limit_minutes * 60:
            log("time limit reached")
            break
        t = time.time()
        try:
            text, real = batch_runner.chat(url, model, j["messages"], schema=j["schema"], **kw)
            err = ""
        except Exception as e:                    # noqa: BLE001 - record and go on
            text, real, err = "", model, f"{type(e).__name__}: {e}"[:300]
        secs = time.time() - t
        try:
            out = batch_runner.parse_json(text) if text else None
        except Exception as e:                    # noqa: BLE001
            out, err = None, err or f"parse: {e}"[:300]
        rec = {"id": j["id"], "case": j["case"], "variant": j["variant"], "key": j["key"],
               "seconds": round(secs, 1), "model": real, "prompt_chars": j["prompt_chars"],
               "raw_chars": len(text), "raw": text, "output": out, "error": err,
               "passages": j["passages"]}
        with open(res_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        n += 1
        v = (out or {}).get("verdict", "ERROR")
        log(f"[{n}] {j['id']} {v} {secs:.0f}s (elapsed {(time.time() - start) / 60:.1f} min)")
    return {"calls": n, "minutes": round((time.time() - start) / 60, 1)}


# ---- report

def report(lib, eval_path: Path, run_dir: Path) -> dict:
    cases = {c["id"]: c for c in load(eval_path)}
    recs = [json.loads(l) for l in (run_dir / "results.jsonl").read_text().splitlines() if l.strip()]
    rows = []
    for r in recs:
        c = cases[r["case"]]
        out = r.get("output") or {}
        v = out.get("verdict")
        rows.append({**{k: r[k] for k in ("id", "case", "variant", "key", "seconds")},
                     "sharing": lib.get(c["key"]).sharing, "type": c["type"], "claim": c["claim"],
                     "ideal": c["ideal"], "acceptable": c["acceptable"], "verdict": v,
                     "ok": v in c["acceptable"], "exact": v == c["ideal"],
                     "confidence": out.get("confidence"), "hedging": out.get("hedging"),
                     "explanation": out.get("explanation", ""), "mismatch": out.get("mismatch", ""),
                     "answered_question": c.get("question"), "doubt": c.get("doubt"),
                     "error": r.get("error"), "has_quote": bool(out.get("quote")),
                     "passage_valid": _ids_valid(out, r["passages"])})
    return {"rows": rows, "summary": summarize(rows)}


def _ids_valid(out: dict, ids: list[str]) -> bool:
    """The reply names passages only by valid ids (`passages`, or the old
    comma-separated `passage` string)."""
    got = out.get("passages")
    if got is None:
        got = [p.strip() for p in re.split(r"[,;\s]+", out.get("passage") or "") if p.strip()]
    return bool(got) and all(isinstance(g, str) and g in ids for g in got)


def summarize(rows):
    def acc(rs):
        return {"n": len(rs), "ok": sum(r["ok"] for r in rs), "exact": sum(r["exact"] for r in rs)}
    s = {"by_variant": {v: acc([r for r in rows if r["variant"] == v]) for v in ("A", "B", "R")}}
    s["by_type"] = {v: {t: acc([r for r in rows if r["variant"] == v and r["type"] == t])
                        for t in sorted({r["type"] for r in rows})} for v in ("A", "B")}
    s["by_sharing"] = {v: {sh: acc([r for r in rows if r["variant"] == v and r["sharing"] == sh])
                           for sh in ("open", "licensed")} for v in ("A", "B")}
    s["confusion"] = {v: Counter(f"{r['ideal']}->{r['verdict']}" for r in rows if r["variant"] == v)
                      for v in ("A", "B", "R")}
    s["seconds"] = {v: _stats([r["seconds"] for r in rows if r["variant"] == v]) for v in ("A", "B", "R")}
    s["errors"] = [r["id"] for r in rows if r["error"] or r["verdict"] is None]
    s["confidence_when_wrong"] = {v: Counter(r["confidence"] for r in rows if r["variant"] == v and not r["ok"])
                                  for v in ("A", "B")}
    s["confidence_when_right"] = {v: Counter(r["confidence"] for r in rows if r["variant"] == v and r["ok"])
                                  for v in ("A", "B")}
    s["passage_ids_valid"] = {v: sum(r["passage_valid"] for r in rows if r["variant"] == v) for v in ("A", "B", "R")}
    s["hedge_flags_B"] = Counter(f"{r['type']}:{r['hedging']}" for r in rows if r["variant"] == "B")
    return s


def _stats(xs):
    xs = sorted(xs)
    if not xs:
        return {}
    return {"n": len(xs), "median": xs[len(xs) // 2], "mean": round(sum(xs) / len(xs), 1),
            "min": xs[0], "max": xs[-1], "total_min": round(sum(xs) / 60, 1)}


# ---- as a cluster batch (litnav eval-model push; results come back with `litnav queue pull`)

def push(lib, eval_path: Path, *, submit: bool = True, log=print) -> dict:
    from . import spool
    cases = load(eval_path)
    jobs = [{**j, "kind": "eval"} for j in order(build_jobs(lib, cases))]
    return spool.push(lib, jobs, kind="eval", meta={"eval": str(eval_path)}, submit=submit, log=log)


def import_batch(lib, b: dict, jobs: dict, fresh: list, *, log=print) -> None:
    """Write a pulled eval batch's new results in `run`'s format, so `report` scores them."""
    if not fresh:
        return
    if not b.get("run_dir"):
        model = fresh[0].get("model", "model").split("/")[-1].replace(":", "-")
        b["run_dir"] = str(lib.root / "derived" / "model-eval" / f"{dt.date.today().isoformat()}-{model}")
    rd = Path(b["run_dir"])
    rd.mkdir(parents=True, exist_ok=True)
    with open(rd / "results.jsonl", "a", encoding="utf-8") as f:
        for r in fresh:
            j = jobs[r["id"]]
            raw = r.get("raw", "")
            out = r.get("output") if isinstance(r.get("output"), dict) else None
            f.write(json.dumps({"id": j["id"], "case": j["case"], "variant": j["variant"], "key": j["key"],
                                "seconds": r.get("seconds"), "model": r.get("model"),
                                "prompt_chars": j["prompt_chars"], "raw_chars": len(raw), "raw": raw,
                                "output": out, "error": r.get("error", ""), "passages": j["passages"]},
                               ensure_ascii=False) + "\n")
    if not (rd / "run.json").exists():
        (rd / "run.json").write_text(json.dumps({"batch": True, "eval": b.get("eval"),
                                                 "model": fresh[0].get("model"), "pushed": b["pushed"]}, indent=1))
    log(f"  eval: {len(fresh)} result(s) into {rd}")
