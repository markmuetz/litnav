"""Evaluate models at whole-paper tasks: `litnav paper-eval`.

Three tasks, on one set of open papers (open, so a Claude judge may read them):

  summary   the paper summary litnav writes (derived.SCHEMA: topic, summary,
            keywords), with the production prompt.
  extract   the paper's own claims as structured records (hk27's first step):
            standalone, in the model's own words, with numbers, conditions,
            how strongly the paper states it, and the passage ids it rests on.
  mentions  the claim graph's mention records (claims.py, the production
            prompt): read section by section, the whole paper; claim type
            (shown / restated / ...), evidence type, conditions, hedging.

Steps:
  pick      choose the papers (eval/paper-eval.jsonl)
  gold      Claude writes each paper's key points once, from the full text:
            what a good summary or claim list must cover (recorded with the
            model and prompt that wrote them)
  run       run a task on [local_model] (or `push` it to the cluster spool;
            `litnav queue pull` brings it back)
  baseline  the summaries already in the library, as a run of their own
  judge     Claude grades each output against the paper and the key points
  report    scores; every run is recorded in eval/runs.jsonl with its model,
            server, prompts and judge, so later models can be compared

Run folders (derived/paper-eval/<task>-<date>-<model>/) hold the jobs (paper
text), raw replies and judgments: git-ignored.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
import threading
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import batch_runner, claims, copyfix, derived, evalstats, localmodel
from . import sharing as S

PROMPT_VERSION = {"summary": derived.PROMPT_VERSION, "extract": 1, "mentions": claims.PROMPT_VERSION,
                  "gold": 1, "judge": 1}
EVAL_CHARS = 72_000          # the pilot's limit: the same text for every model, fits Qwen3's 32k tokens
                             # with room to think. A run can set its own (`--chars`; 0 = the whole paper)
LONG_PAPER = 72_000          # report splits scores at this paper length (the pilot's cut-off)
JUDGE_CHARS = 160_000
TASKS = ("summary", "extract", "mentions")


# ---- the papers

def pick(lib, n: int = 20, seed: int = 1, *, min_chars=25_000, max_chars=150_000) -> list[dict]:
    """Open papers with clean PDF text (not OCR) of ordinary length, chosen at random."""
    from . import index
    con = index.connect(lib, readonly=True)
    try:
        chunked = {r[0] for r in con.execute("SELECT DISTINCT key FROM chunks")}
    finally:
        con.close()
    pool = []
    for it in sorted(lib.items(), key=lambda i: i.key):
        if not (it.allowed(S.SEND_OUT) and it.has_text and it.key in chunked):
            continue
        if it.meta.get("files", {}).get("text_source", "").startswith("ocr"):
            continue
        size = it.text_path.stat().st_size
        if min_chars <= size <= max_chars:
            pool.append({"key": it.key, "title": it.title, "year": it.year, "chars": size})
    random.Random(seed).shuffle(pool)
    return sorted(pool[:n], key=lambda p: p["key"])


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text("utf-8").splitlines() if l.strip()]


def save(path: Path, rows: list[dict]) -> None:
    Path(path).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


# ---- Claude: gold key points and judging (open papers only)

GOLD_SCHEMA = {
    "type": "object",
    "properties": {"key_points": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "point": {"type": "string"},
        "kind": {"type": "string", "enum": ["aim", "method", "data", "finding", "conclusion"]},
        "core": {"type": "boolean"}}, "required": ["id", "point", "kind", "core"]}}},
    "required": ["key_points"],
}
GOLD_SYSTEM = (
    "You write the reference for grading model-written summaries and claim lists of an academic paper. "
    "From the paper's text, list its key points: 6-12 specific statements of what this paper itself does "
    "and finds (its aims, method and data in brief, and above all its findings and conclusions), with the "
    "numbers, directions and conditions that matter. Not background from other work. Mark as core the "
    "3-6 points any good summary must convey. Ids k1, k2, ... In your own words; no quotes.")


def _claude(lib, it, system, prompt, schema, model):
    derived.outbound(lib, [it])                      # open papers only
    env = derived.run_claude(derived.claude_code_args(model, schema, system=system), prompt)
    out = env.get("structured_output") or env.get("result")
    if isinstance(out, str):
        out = batch_runner.parse_json(out)
    return out, env.get("modelUsage") and list(env["modelUsage"])[0] or model


GEMINI_MODEL = "gemini-4-neon-high"      # Mark's choice for the second judge, 23 Nov 2026
JUDGES = ("claude", "gemini")


def _gemini(lib, it, system, prompt, schema, model, timeout=1200):
    """One structured call to Gemini through the `agy` CLI, from an empty folder.
    agy keeps its conversations (no setting turns that off), so like the Claude
    judge it sees open papers only. The prompt goes in argv: agy -p does not
    take it from stdin."""
    import shutil
    import subprocess
    import tempfile
    derived.outbound(lib, [it])                      # open papers only
    if not shutil.which("agy"):
        raise RuntimeError("the agy CLI is not installed")
    cmd = ["agy", "-p", f"{system}\n\n{prompt}", "--output-format", "json", "--json-schema", json.dumps(schema),
           "--model", model, "--sandbox", "--disable-slash-commands"]
    with tempfile.TemporaryDirectory(prefix="litnav-agy-") as td:
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=td, timeout=timeout, stdin=subprocess.DEVNULL)
    try:
        env = json.loads(p.stdout)
    except json.JSONDecodeError:
        env = {"status": "ERROR", "error": (p.stderr or p.stdout or "no output").strip()[:300]}
    out = env.get("structured_output")
    if not isinstance(out, dict):
        msg = str(env.get("error") or p.stderr or "")[:300]
        if derived._FATAL.search(msg) or "RESOURCE_EXHAUSTED" in (p.stderr or ""):
            raise derived.Stop(f"agy: {msg}")        # a quota or login error repeats for every paper
        raise RuntimeError(f"agy: no structured output (status {env.get('status')}){': ' + msg if msg else ''}")
    return out, model


def judgments_path(run_dir: Path, judge: str = "claude") -> Path:
    return run_dir / ("judgments.jsonl" if judge == "claude" else f"judgments-{judge}.jsonl")


def gold(lib, eval_path: Path, *, model="opus", jobs=4, log=print) -> int:
    rows = load(eval_path)
    todo = [r for r in rows if not r.get("key_points")]

    def one(r):
        it = lib.get(r["key"])
        text = it.text()[:JUDGE_CHARS]
        out, real = _claude(lib, it, GOLD_SYSTEM, f"Paper: {it.title} ({it.year})\n\nText:\n{text}",
                            GOLD_SCHEMA, model)
        r["key_points"] = out["key_points"]
        r["gold"] = {"model": real, "prompt_version": PROMPT_VERSION["gold"],
                     "date": dt.date.today().isoformat(), "chars_read": len(text)}
        log(f"gold {r['key']}: {len(r['key_points'])} key points")
    with ThreadPoolExecutor(jobs) as ex:
        list(ex.map(one, todo))
    save(eval_path, rows)
    return len(todo)


# ---- the model jobs

EXTRACT_SYSTEM = (
    "You extract the claims an academic paper makes, for a database that later checks each claim against "
    "the paper. You see the paper's text as numbered passages (from the PDF: noisy). List the paper's own "
    "claims: its findings, conclusions, and the key facts of its method and data. Not background claims "
    "it cites from other work. 8-25 claims, the most important first.\n"
    "Each claim: one standalone sentence a reader understands without the paper (name the model, region "
    "or quantity; no 'it' or 'this study'); in your own words, never copied sentences; keep every number "
    "with its units, the direction of each effect, and the conditions it holds under (model, region, "
    "period, resolution). `strength` is how the paper states it: shown (demonstrated by its results), "
    "suggested (it says may, suggests, likely), or speculated. `passages`: the ids of the passages that "
    "state it. Reply with JSON only.")


def extract_schema(ids: list[str]) -> dict:
    return {"type": "object", "properties": {"claims": {"type": "array", "maxItems": 30, "items": {
        "type": "object", "properties": {
            "claim": {"type": "string"},
            "kind": {"type": "string", "enum": ["finding", "conclusion", "method", "data"]},
            "numbers": {"type": "array", "items": {"type": "string"}},
            "conditions": {"type": "string"},
            "strength": {"type": "string", "enum": ["shown", "suggested", "speculated"]},
            "passages": {"type": "array", "maxItems": 4, "items": {"type": "string", "enum": ids}}},
        "required": ["claim", "kind", "numbers", "conditions", "strength", "passages"],
        "additionalProperties": False}}}, "required": ["claims"], "additionalProperties": False}


def chunks_for(lib, it, limit: int | None = EVAL_CHARS) -> list[dict]:
    """The paper's passages in order, within `limit` characters (0 or None: all of them)."""
    from . import index
    con = index.connect(lib, readonly=True)
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT id, section_title, kind, text FROM chunks WHERE key=? ORDER BY ord", (it.key,))]
    finally:
        con.close()
    out, budget = [], limit or float("inf")
    for r in rows:
        if budget <= 0:
            break
        t = r["text"] if budget == float("inf") else r["text"][:int(budget)]
        budget -= len(t) + len(r["id"]) + 8
        out.append({"id": r["id"], "section": r["section_title"] or r["kind"] or "", "text": t})
    return out


def build_job(lib, key: str, task: str, chars: int | None = EVAL_CHARS) -> dict:
    it = lib.get(key)
    localmodel.gate(it)
    if task == "summary":
        j = localmodel.summarize_job(it, limit=chars or 10 ** 9)
        return {"id": f"summary-{key}", "kind": "eval", "task": task, "key": key, "schema": j["schema"],
                "messages": j["messages"], "chars_sent": j["chars_sent"], "passages": j.get("passages", []),
                "followup": j.get("followup"), "title": j.get("title", "")}
    ps = chunks_for(lib, it, chars)
    body = "\n\n".join(f"[{p['id']}] ({p['section']})\n{p['text']}" for p in ps)
    ids = [p["id"] for p in ps]
    return {"id": f"extract-{key}", "kind": "eval", "task": task, "key": key, "schema": extract_schema(ids),
            "messages": [{"role": "system", "content": EXTRACT_SYSTEM},
                         {"role": "user", "content": f"Paper: {it.title} ({it.year})\n\nPassages:\n{body}"
                                                     "\n\nReply with JSON only."}],
            "chars_sent": sum(len(p["text"]) for p in ps), "passages": ids}


def default_run_dir(lib, task: str, model: str) -> Path:
    m = re.sub(r"[^\w.-]+", "-", model.split("/")[-1])
    return lib.root / "derived" / "paper-eval" / f"{task}-{dt.date.today().isoformat()}-{m}"


def write_jobs(lib, eval_path: Path, task: str, run_dir: Path, chars: int | None = EVAL_CHARS) -> list[dict]:
    if task == "mentions":                       # several calls a paper; chars = passages per call
        jobs = []
        for r in load(eval_path):
            it = lib.get(r["key"])
            localmodel.gate(it)
            jobs += claims.jobs(lib, it, budget=chars or claims.GROUP_CHARS)
    else:
        jobs = [build_job(lib, r["key"], task, chars) for r in load(eval_path)]
    run_dir.mkdir(parents=True, exist_ok=True)
    save(run_dir / "jobs.jsonl", jobs)
    return jobs


def _chars_for_run(run_dir: Path, chars: int | None) -> int | None:
    """A run's character limit is fixed when its jobs are written: resuming
    keeps it, and asking for a different one is an error."""
    p = run_dir / "run.json"
    if (run_dir / "jobs.jsonl").exists() and p.is_file():
        had = json.loads(p.read_text()).get("eval_chars", EVAL_CHARS)
        if chars is not None and chars != had:
            raise ValueError(f"{run_dir} was written with --chars {had}; use a new --run-dir for {chars}")
        return had
    return EVAL_CHARS if chars is None else chars


def default_chars(task: str, chars: int | None) -> int | None:
    if chars is None and task == "mentions":
        return claims.GROUP_CHARS
    return chars


def reask_jobs(lib, run_dir: Path, task: str) -> list[dict]:
    """Calls whose reply copied from the paper, asked once more as production
    does (summary and mentions): written to reask.jsonl."""
    if task not in ("summary", "mentions"):
        return []
    res = {}
    for r in load(run_dir / "results.jsonl") if (run_dir / "results.jsonl").exists() else []:
        if r["id"] not in res or r.get("ok"):
            res[r["id"]] = r
    out = []
    for j in load(run_dir / "jobs.jsonl"):
        r = res.get(j["id"], {})
        o = r.get("output")
        if not (r.get("ok") and isinstance(o, dict)) or f"{j['id']}-r" in res:
            continue
        it = lib.get(j["key"])
        if task == "summary":
            err = derived.check(it, o)
            if err and err.startswith("copies"):
                out.append(localmodel.rephrase_job(j, o, err))
        else:
            bad = claims.copied(it, o)
            if bad:
                out.append(claims.rephrase_job(copyfix.fill(j, res), o, bad))
    if task == "mentions":                  # phase 2: check each usable rewrite's meaning
        rjobs = {r["id"]: r for r in load(run_dir / "reask.jsonl")} if (run_dir / "reask.jsonl").exists() else {}
        for j in load(run_dir / "jobs.jsonl"):
            rid = f"{j['id']}-r"
            again, first = res.get(rid, {}), res.get(j["id"], {})
            if f"{j['id']}-c" in res or not (again.get("ok") and isinstance(again.get("output"), dict)):
                continue
            asked = (rjobs.get(rid) or {}).get("rewrite") or again.get("rewrite")
            if not asked or "rewrites" not in again["output"]:
                continue                      # an old whole-reply re-ask: nothing to check
            j = copyfix.fill(j, res)
            cand = claims.candidate_rewrites(first.get("output"), again["output"], asked)
            still = {b["n"] for b in claims.copied(lib.get(j["key"]), {"mentions": [
                {"statement": cand.get(n, "")} for n in range(1, max(cand, default=0) + 1)]}) if b["n"] in cand}
            cand = {n: t for n, t in cand.items() if n not in still}      # a rewrite that still copies: not used
            if cand:
                out.append(claims.meaning_job(j, first["output"], cand))
    if out:
        prev = load(run_dir / "reask.jsonl") if (run_dir / "reask.jsonl").exists() else []
        ids = {o["id"] for o in out}
        save(run_dir / "reask.jsonl", [r for r in prev if r["id"] not in ids] + out)
    return out


def _pending(run_dir: Path, jobs: list[dict]) -> Path:
    """The jobs of this round alone, for the runner (reask.jsonl keeps them all)."""
    p = run_dir / "reask-round.jsonl"
    save(p, jobs)
    return p


def reask_local(lib, run_dir: Path, task: str, url, model, *, log=print, **kw) -> int:
    """Rewrites, then their meaning checks, on the local endpoint."""
    n = 0
    for _ in range(2):
        rj = reask_jobs(lib, run_dir, task)
        if not rj:
            break
        log(f"asking {len(rj)} call(s) again: "
            + ("checking rewrites" if all("meaning_check" in j for j in rj) else "they copied from the paper"))
        batch_runner.run(_pending(run_dir, rj), run_dir / "results.jsonl", url, model, log=log, **kw)
        n += len(rj)
    return n


def run(lib, eval_path: Path, task: str, run_dir: Path | None = None, *, chars: int | None = None,
        log=print) -> Path:
    """chars: characters of each paper the model reads (default EVAL_CHARS; 0 = the whole paper)."""
    url, model, kw = localmodel.endpoint(lib)
    run_dir = run_dir or default_run_dir(lib, task, model)
    chars = _chars_for_run(run_dir, default_chars(task, chars))
    if not (run_dir / "jobs.jsonl").exists():
        write_jobs(lib, eval_path, task, run_dir, chars)
    write_run_meta(run_dir, task=task, eval_path=eval_path, model=model, via="local", eval_chars=chars,
                   model_info=localmodel.model_info(lib), profile=lib.local_model.get("profile"))
    batch_runner.run(run_dir / "jobs.jsonl", run_dir / "results.jsonl", url, model, log=log, **kw)
    reask_local(lib, run_dir, task, url, model, log=log, **kw)
    return run_dir


def push_reask(lib, run_dir: Path, *, submit=True, log=print) -> dict | None:
    """After a pulled cluster run: send its re-asks as a batch into the same run folder.
    Mentions take two pushes: the rewrites, then (after pulling them) their meaning checks."""
    from . import spool
    task = json.loads((run_dir / "run.json").read_text())["task"]
    rj = reask_jobs(lib, run_dir, task)
    if not rj:
        return None
    return spool.push(lib, rj, kind="paper-eval", meta={"run_dir": str(run_dir), "task": task}, submit=submit,
                      log=log)


def rerun_jobs(run_dir: Path, keys: list[str]) -> list[dict]:
    """Every job of these papers in a run, with their results (follow-ups too) set
    aside in results.rerun-<time>.jsonl: to run them again into the same folder,
    e.g. a paper whose experiments call failed, so its section calls ran without
    the table."""
    jobs = [j for j in load(run_dir / "jobs.jsonl") if j["key"] in set(keys)]
    ids = {j["id"] for j in jobs}
    rp = run_dir / "results.jsonl"
    if rp.exists() and jobs:
        rows = load(rp)
        out = [r for r in rows if r["id"] in ids or r["id"][:-2] in ids]
        if out:
            save(run_dir / f"results.rerun-{dt.datetime.now():%Y%m%d-%H%M%S}.jsonl", out)
            save(rp, [r for r in rows if not (r["id"] in ids or r["id"][:-2] in ids)])
    return jobs


def push_rerun(lib, run_dir: Path, keys: list[str], *, submit=True, log=print) -> dict | None:
    from . import spool
    task = json.loads((run_dir / "run.json").read_text())["task"]
    jobs = rerun_jobs(run_dir, keys)
    if not jobs:
        return None
    return spool.push(lib, jobs, kind="paper-eval", meta={"run_dir": str(run_dir), "task": task}, submit=submit,
                      log=log)


def push(lib, eval_path: Path, task: str, *, model_hint="batch", chars: int | None = None, submit=True,
         log=print) -> dict:
    from . import spool
    chars = default_chars(task, chars)
    chars = EVAL_CHARS if chars is None else chars
    rd = lib.root / "derived" / "paper-eval" / f"{task}-{dt.datetime.now():%Y-%m-%d-%H%M}-{model_hint}"
    jobs = write_jobs(lib, eval_path, task, rd, chars)
    write_run_meta(rd, task=task, eval_path=eval_path, model=None, via="batch", eval_chars=chars)
    return spool.push(lib, jobs, kind="paper-eval", meta={"run_dir": str(rd), "task": task}, submit=submit,
                      log=log)


def import_batch(lib, b: dict, jobs: dict, fresh: list, server: dict | None, *, log=print) -> None:
    rd = Path(b["run_dir"])
    with open(rd / "results.jsonl", "a", encoding="utf-8") as f:
        for r in fresh:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if fresh:
        m = json.loads((rd / "run.json").read_text())
        m["model"] = m.get("model") or fresh[0].get("model")
        if server:
            m["model_info"] = server
        (rd / "run.json").write_text(json.dumps(m, indent=1))
    log(f"  {b['task']}: {len(fresh)} result(s) into {rd}")


def baseline(lib, eval_path: Path, run_dir: Path | None = None) -> Path:
    """The summaries already in the library, scored like any run."""
    rd = run_dir or lib.root / "derived" / "paper-eval" / f"summary-{dt.date.today().isoformat()}-existing"
    rd.mkdir(parents=True, exist_ok=True)
    res, models = [], Counter()
    for r in load(eval_path):
        s = lib.get(r["key"]).summary() or {}
        p = s.get("provenance", {})
        models[p.get("model") or p.get("source", "?")] += 1
        res.append({"id": f"summary-{r['key']}", "ok": bool(s), "model": p.get("model"),
                    "output": {k: s.get(k) for k in ("topic", "summary", "keywords")}, "provenance": p})
    save(rd / "results.jsonl", res)
    save(rd / "jobs.jsonl", [{"id": x["id"], "task": "summary", "key": x["id"][8:], "passages": []} for x in res])
    write_run_meta(rd, task="summary", eval_path=eval_path, model="existing library summaries",
                   via="library", model_info={"models": dict(models)})
    return rd


def write_run_meta(rd: Path, **kw) -> None:
    p = rd / "run.json"
    m = json.loads(p.read_text()) if p.is_file() else {}
    eval_path = kw.pop("eval_path", None)
    if eval_path:
        kw["eval"] = str(eval_path)
        kw["eval_sha256"] = sha(eval_path)
    kw.setdefault("prompt_version", PROMPT_VERSION[kw.get("task", m.get("task"))])
    kw.setdefault("eval_chars", EVAL_CHARS)
    m.setdefault("created", dt.datetime.now().isoformat(timespec="seconds"))
    m.update({k: v for k, v in kw.items() if v is not None or k not in m})
    rd.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, indent=1))


# ---- judging

JUDGE_SYSTEM = (
    "You grade a model's output about an academic paper against the paper itself, for an evaluation. Be "
    "strict about numbers, units, directions and conditions, and about statements stronger or more "
    "general than the paper. Judge only from the paper's text given. Reply with the JSON asked for.")
_VERDICT = {"type": "string"}
SUMMARY_JUDGE = {"type": "object", "properties": {
    "statements": {"type": "array", "items": {"type": "object", "properties": {
        "statement": {"type": "string"},
        "verdict": {"type": "string", "enum": ["supported", "overstated", "wrong", "unsupported"]},
        "note": {"type": "string"}}, "required": ["statement", "verdict", "note"]}},
    "key_points_covered": {"type": "array", "items": {"type": "string"}},
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "n": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["correct", "wrong_detail", "overstated", "not_in_paper",
                                               "trivial"]},
        "kind": {"type": "string", "enum": derived.ITEM_KINDS},
        "hedging": {"type": "string", "enum": derived.HEDGING},
        "standalone": {"type": "boolean"},
        "note": {"type": "string"}}, "required": ["n", "verdict", "kind", "hedging", "standalone", "note"]}},
    "key_points_covered_all": {"type": "array", "items": {"type": "string"}},
    "keywords_ok": {"type": "boolean"}},
    "required": ["statements", "key_points_covered", "items", "key_points_covered_all", "keywords_ok"]}
EXTRACT_JUDGE = {"type": "object", "properties": {
    "claims": {"type": "array", "items": {"type": "object", "properties": {
        "n": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["correct", "wrong_detail", "overstated", "not_in_paper",
                                               "background", "trivial"]},
        "strength_ok": {"type": "boolean"},
        "standalone": {"type": "boolean"},
        "note": {"type": "string"}}, "required": ["n", "verdict", "strength_ok", "standalone", "note"]}},
    "key_points_covered": {"type": "object", "additionalProperties": {"type": "array",
                                                                       "items": {"type": "integer"}}}},
    "required": ["claims", "key_points_covered"]}


MENTIONS_JUDGE = {"type": "object", "properties": {
    "claims": {"type": "array", "items": {"type": "object", "properties": {
        "n": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["correct", "wrong_detail", "overstated", "not_in_paper",
                                               "trivial"]},
        "claim_type": {"type": "string", "enum": claims.CLAIM_TYPES},
        "evidence_type": {"type": "string", "enum": claims.EVIDENCE_TYPES},
        "hedging": {"type": "string", "enum": claims.HEDGING},
        "standalone": {"type": "boolean"},
        "cites_ok": {"type": "string", "enum": ["right", "wrong", "missing", "none_needed", "not_given"]},
        "note": {"type": "string"}},
        "required": ["n", "verdict", "claim_type", "evidence_type", "hedging", "standalone", "note"]}},
    "key_points_covered": {"type": "object", "additionalProperties": {"type": "array",
                                                                       "items": {"type": "integer"}}},
    "runs": {"type": "array", "items": {"type": "object", "properties": {
        "n": {"type": "integer"}, "verdict": {"type": "string", "enum": ["correct", "wrong_detail", "not_in_paper"]},
        "wrong": {"type": "array", "items": {"type": "string"}}, "note": {"type": "string"}},
        "required": ["n", "verdict", "wrong", "note"]}},
    "runs_missed": {"type": "array", "items": {"type": "string"}}},
    "required": ["claims", "key_points_covered"]}
JUDGE_SCHEMA = {"summary": SUMMARY_JUDGE, "extract": EXTRACT_JUDGE, "mentions": MENTIONS_JUDGE}
JUDGE_VERSION = {"summary": 2, "extract": 1, "mentions": 7}   # mentions 4: grades the experiments table (v4) too;
# 5: evidence rule 2 (coarser than 10 km and not stated -> parametrized; idealized_model); 6: grades cites
JUDGE_EVIDENCE_RULE = {4: 1, 5: 2, 6: 2, 7: 3}   # the evidence rule each mentions judge version labels by (older: 1)


def _mention_line(n, m, runs=None) -> str:
    c = m.get("conditions") or {}
    e = m.get("experiment") or {}
    v = m.get("value") or {}
    bits = [f"chunk {m.get('chunk')}", f"source {m.get('source')}", f"claim_type {m.get('claim_type')}",
            f"evidence_type {claims.evidence_type(m, runs) if 'evidence' in m else m.get('evidence_type')}",
            *([f"experiment '{claims.mention_experiment(m)}'"] if claims.mention_experiment(m) else []),
            *([f"cites '{m['cites']}'"] if "cites" in m else []),
            f"hedging {m.get('hedging')}" + (f" ('{m['hedge_words']}')" if m.get("hedge_words") else "")]
    cond = "; ".join(f"{k} {x}" for k, x in {**e, **c}.items() if x and k not in ("run_name",))
    if cond:
        bits.append(cond)
    if str(v.get("value", "")).strip():
        bits.append(f"value {v['value']} {v.get('units', '')} [{v.get('low', '')}, {v.get('high', '')}]")
    return f"{n}. {m.get('statement')} [{'; '.join(bits)}]"


def judge_prompt(task, it, text, kps, out) -> str:
    kp = "\n".join(f"{k['id']}{' (core)' if k.get('core') else ''}: {k['point']}" for k in kps)
    head = f"Paper: {it.title} ({it.year})\n\nThe paper's key points (reference):\n{kp}\n\n"
    if task == "summary":
        items = out.get("key_items") or []
        listing = "\n".join(f"{n}. [{x.get('kind')}; hedging {x.get('hedging')}] {x.get('statement')}"
                            + (f" (conditions: {x['conditions']})" if x.get("conditions") else "")
                            for n, x in enumerate(items, 1)) or "(none)"
        body = (f"Model's summary:\nTopic: {out.get('topic')}\nSummary: {out.get('summary')}\n"
                f"Keywords: {', '.join(out.get('keywords') or [])}\nKey items:\n{listing}\n\n"
                "1. Split the topic and summary (the prose, not the key items) into their factual "
                "statements. Give each a verdict: supported (the paper says it), overstated (more certain or "
                "general than the paper), wrong (a number, direction, method or condition differs), "
                "unsupported (not in the paper).\n"
                "2. key_points_covered: the ids of the key points the topic and summary prose substantially "
                "convey.\n"
                "3. items: for each key item n, a verdict: correct; wrong_detail (a number, unit, direction, "
                "method or condition differs); overstated (stronger or more general than the paper); "
                "not_in_paper; trivial (true but says nothing useful). Then its right kind (method, finding, "
                "conclusion, limitation) and hedging (strong: shows, demonstrates; moderate: suggests, "
                "indicates; weak: may, could), from the paper, whatever the model said; and standalone: is "
                "it understandable without the paper? An empty list if there are no key items.\n"
                "4. key_points_covered_all: the ids of the key points conveyed by the prose and the key items "
                "together.\n"
                "5. keywords_ok: are the keywords topical and lower-case?\n")
    elif task == "mentions":
        runs = out.get("runs")
        lines = "\n".join(_mention_line(n, m, runs) for n, m in enumerate(out.get("mentions") or [], 1))
        rt = ""
        if runs is not None:
            rt = ("Model's experiments table (the paper's model runs and observational analyses):\n"
                  + "\n".join(f"R{n}. {json.dumps({k: v for k, v in r.items() if k != 'components'}, ensure_ascii=False)}"
                               + (" components: " + "; ".join(
                                   f"{c.get('kind')}={c.get('state')}" + (f" ({c['scheme']})" if c.get("scheme") else "")
                                   for c in r.get("components") or []) if r.get("components") else "")
                               for n, r in enumerate(runs, 1)) + "\n\n")
        body = (rt + f"Model's extracted claims (mentions), each with its labels:\n{lines}\n\n"
                "1. For each claim n give a verdict on its statement: correct; wrong_detail (a number, unit, "
                "direction, method or condition differs from the paper); overstated (stronger or more general "
                "than the paper); not_in_paper; trivial (true but says nothing useful). A claim the paper "
                "takes from other work is fine if the paper says it; judge its label below.\n"
                "   Then give the right labels, from the paper, whatever the model said:\n"
                "   claim_type: shown (this paper's own results demonstrate it); restated (the paper attributes "
                "it to other work, usually with a citation, or states it as known background); assumed (a "
                "premise or method choice taken as given); speculation (offered without support here). The paper's "
                "own result compared with cited work is shown.\n"
                "   evidence_type, from what THIS paper states (not from what you know about a model): "
                "observations; reanalysis; parametrized_model (the paper says a deep-convection scheme is "
                "used); les or cpm (the paper says deep convection is explicit and calls the run a large-eddy "
                "simulation, or convection-permitting, cloud-resolving or storm-resolving, or classes it so by "
                "its own definition); explicit_model (explicit, but the paper doesn't class it); "
                "idealized_model for a simplified dynamical model (quasi-geostrophic, shallow-water, dry or "
                "two-layer, conceptual); theory; mixed; unknown (the paper doesn't say whether deep convection "
                "is parametrized, or a machine-learning model).\n"
                "   hedging: strong (shows, demonstrates); moderate (suggests, indicates, likely); weak (may, "
                "could, possibly): how firmly the paper states it.\n"
                "   standalone: is the statement understandable without the paper?\n"
                + ("   cites_ok, where the claim shows cites: right (the citations the paper attaches to this "
                   "claim), wrong (a different or extra work), missing (the paper cites work for it but cites "
                   "is empty or incomplete), none_needed (empty, and the paper cites nothing for it).\n"
                   if any("cites" in m for m in out.get("mentions") or []) else "   cites_ok: not_given.\n")
                + "2. key_points_covered: for each key point id, the numbers of the claims that convey it "
                "(an empty list if none).\n"
                + ("3. runs: for each entry Rn of the experiments table: verdict correct (every field, "
                   "component and dataset matches the paper), wrong_detail (some differ: list them in wrong, "
                   "e.g. 'kind', 'horizontal_grid_spacing', 'microphysics.scheme', 'deep_convection.state', "
                   "'datasets.version', 'method'), or not_in_paper (no such experiment in this paper, e.g. a "
                   "dataset listed as a model run, or another paper's run). A component the paper doesn't "
                   "describe is right as not_stated. runs_missed: the names of model runs and observational "
                   "analyses the paper itself makes that the table leaves out (an empty list if none).\n"
                   if runs is not None else ""))
    else:
        listing = "\n".join(f"{n}. {c.get('claim')} [kind {c.get('kind')}; strength {c.get('strength')}; "
                           f"conditions: {c.get('conditions') or '-'}]"
                           for n, c in enumerate(out.get("claims") or [], 1))
        body = (f"Model's extracted claims:\n{listing}\n\n"
                "1. For each claim n give a verdict: correct; wrong_detail (a number, unit, direction, method "
                "or condition differs from the paper); overstated (stronger or more general than the paper); "
                "not_in_paper; background (true, but a result of other work the paper cites, not this "
                "paper's); trivial (true but says nothing useful). strength_ok: does its strength (shown / "
                "suggested / speculated) match how the paper states it? standalone: is it understandable "
                "without the paper?\n"
                "2. key_points_covered: for each key point id, the numbers of the claims that convey it "
                "(an empty list if none).\n")
    return head + body + f"\nPaper text:\n{text}"


def paper_results(run_dir: Path, task: str) -> dict:
    """One result per paper: a task with several calls a paper (mentions) is
    merged, and counts as done only when every call is."""
    jobs = load(run_dir / "jobs.jsonl")
    res = {}
    if (run_dir / "results.jsonl").exists():
        for r in load(run_dir / "results.jsonl"):
            if r["id"] not in res or r.get("ok"):          # a later success replaces a failure
                res[r["id"]] = r
    rjobs = {r["id"]: r for r in load(run_dir / "reask.jsonl")} if (run_dir / "reask.jsonl").exists() else {}
    out = {}
    for j in jobs:
        first = res.get(j["id"], {})
        again = res.get(f"{j['id']}-r", {})
        rw = None
        if (task == "mentions" and again.get("ok") and isinstance(again.get("output"), dict)
                and "rewrites" in again["output"] and isinstance(first.get("output"), dict)):
            chk = res.get(f"{j['id']}-c", {})
            merged, rw = claims.merge_rewrites(first["output"], again["output"],
                                               (rjobs.get(f"{j['id']}-r") or {}).get("rewrite")
                                               or again.get("rewrite") or [],
                                               chk.get("output") if chk.get("ok") else None,
                                               (rjobs.get(f"{j['id']}-c") or {}).get("meaning_check")
                                               or chk.get("meaning_check"))
            again = {**first, "output": merged,
                     "seconds": (first.get("seconds") or 0) + (again.get("seconds") or 0)
                     + (chk.get("seconds") or 0)}
        r = again if again.get("ok") and isinstance(again.get("output"), dict) else first
        pid = f"{task}-{j['key']}" if task == "mentions" else j["id"]
        if j.get("role") == "runs":               # the paper's runs table (v4), not a group of mentions
            p = out.setdefault(pid, {"id": pid, "key": j["key"], "ok": True, "seconds": 0.0, "passages": [],
                                     "chars_sent": 0, "parts": [], "first_parts": [], "reasked": 0,
                                     "rewrites": {"asked": 0, "candidates": 0, "used": 0}})
            o = first.get("output") if first.get("ok") and isinstance(first.get("output"), dict) else {}
            p["runs"] = claims.table(o) or []
            p["runs_ok"] = bool(first.get("ok"))
            p["seconds"] = round(p["seconds"] + (first.get("seconds") or 0), 1)
            p["chars_sent"] += j.get("chars_sent") or 0
            continue
        p = out.setdefault(pid, {"id": pid, "key": j["key"], "ok": True, "seconds": 0.0, "passages": [],
                                 "chars_sent": 0, "parts": [], "first_parts": [], "reasked": 0,
                                 "rewrites": {"asked": 0, "candidates": 0, "used": 0}})
        p["reasked"] += r is again
        for k, v in (rw or {}).items():
            p["rewrites"][k] += v
        p["first_parts"].append(first.get("output") if isinstance(first.get("output"), dict) else None)
        good = bool(r.get("ok")) and isinstance(r.get("output"), dict)
        p["ok"] = p["ok"] and good
        p["seconds"] = round(p["seconds"] + (r.get("seconds") or 0), 1) if r else p["seconds"]
        p["passages"] += j.get("passages", [])
        p["chars_sent"] += j.get("chars_sent") or 0
        p["parts"].append(r.get("output") if good else None)
    for p in out.values():
        if task == "mentions":
            p["output"] = {"mentions": [m for o in p["parts"] if o for m in o.get("mentions") or []],
                           **({"runs": p["runs"]} if "runs" in p else {})} if p["ok"] and p["parts"] else None
        else:
            p["output"] = p["parts"][0] if p["ok"] else None
        if not any(p["parts"]):
            p["seconds"] = None
    return out


def judge(lib, eval_path: Path, run_dir: Path, *, model=None, jobs=4, judge_with="claude", log=print) -> int:
    """Grade each paper's output; judge_with claude (default) or gemini, each
    into its own judgments file."""
    meta = json.loads((run_dir / "run.json").read_text())
    task = meta["task"]
    cases = {r["key"]: r for r in load(eval_path)}
    model = model or ("opus" if judge_with == "claude" else GEMINI_MODEL)
    call = _claude if judge_with == "claude" else _gemini
    jpath = judgments_path(run_dir, judge_with)
    have = {r["id"] for r in load(jpath)} if jpath.exists() else set()
    todo = [r for r in paper_results(run_dir, task).values() if r["ok"] and r["id"] not in have]

    stop = threading.Event()

    def one(r):
        if stop.is_set():                            # a quota or login error: skip the rest
            return None
        key = r["key"]
        it = lib.get(key)
        text = it.text()[:JUDGE_CHARS]
        try:
            out, real = call(lib, it, JUDGE_SYSTEM, judge_prompt(task, it, text, cases[key]["key_points"],
                                                                     r["output"]),
                                JUDGE_SCHEMA[task], model)
        except derived.Stop:
            stop.set()
            raise
        except Exception as e:                       # noqa: BLE001 - record, go on
            log(f"judge {r['id']}: {type(e).__name__}: {e}")
            return None
        d = {"id": r["id"], "key": key, "judge_model": real, "judge_prompt_version": JUDGE_VERSION[task],
             "judgment": out}
        # saved as each finishes, so a run stopped part-way keeps its judgments
        # and a rerun judges only the rest
        with lock, open(jpath, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
        log(f"judged {r['id']}")
        return d
    lock = threading.Lock()
    with ThreadPoolExecutor(jobs) as ex:
        done = [x for x in ex.map(one, todo) if x]
    if done:
        info = {"model": done[0]["judge_model"], "prompt_version": JUDGE_VERSION[task]}
        m = json.loads((run_dir / "run.json").read_text())
        judges = {**(m.get("judges") or {}), judge_with: info}
        write_run_meta(run_dir, judges=judges, **({"judge": info} if judge_with == "claude" else {}))
    return len(done)


# ---- scoring

copied_run = derived.copied_run        # one definition, shared with the summary writer


def _ngrams(text, n=6):
    return derived.ngrams(text, n)


def report(lib, eval_path: Path, run_dir: Path, judge_with: str = "claude") -> dict:
    meta = json.loads((run_dir / "run.json").read_text())
    task = meta["task"]
    cases = {r["key"]: r for r in load(eval_path)}
    jp = judgments_path(run_dir, judge_with)
    jud = {r["id"]: r["judgment"] for r in load(jp)} if jp.exists() else {}
    jver = {r["id"]: r.get("judge_prompt_version") for r in load(jp)} if jp.exists() else {}
    if judge_with != "claude":
        meta = {**meta, "judge": (meta.get("judges") or {}).get(judge_with)}
    rows = []
    for jid, r in paper_results(run_dir, task).items():
        key = r["key"]
        out = r["output"]
        kps = cases[key].get("key_points", [])
        core = {k["id"] for k in kps if k.get("core")}
        text = lib.get(key).text()
        row = {"id": jid, "key": key, "ok": out is not None, "seconds": r.get("seconds"), "judged": jid in jud,
               "paper_chars": len(text), "chars_sent": r["chars_sent"], "calls": len(r["parts"]),
               "reasked": r.get("reasked", 0), "rewrites": r.get("rewrites") or {}}
        if out is not None:
            grams = _ngrams(text)
            if task == "summary":
                ki = out.get("key_items") or []
                cs = derived.copy_stats(lib.get(key), out)
                row["copied_run"], row["shared_6grams"] = cs["copied_run"], cs["shared_6grams"]
                row["sentences"] = len(re.findall(r"[.!?](\s|$)", out.get("summary") or ""))
                row["words"] = len((out.get("summary") or "").split())
                row["items"] = len(ki)
                row["items_located"] = sum(bool(x.get("chunks")) and all(c in r["passages"] for c in x["chunks"])
                                           for x in ki)
                row["item_labels"] = [{"kind": x.get("kind"), "hedging": x.get("hedging")} for x in ki]
            elif task == "mentions":
                ms = out.get("mentions") or []
                row["claims"] = len(ms)
                row.update(_copy_row([m.get("statement", "") for m in ms], grams, lib.get(key).title))
                first = [m.get("statement", "") for o in r.get("first_parts") or [] if o
                         for m in o.get("mentions") or []]
                row["copied_12_first"] = _copy_row(first, grams, lib.get(key).title)["copied_12"]
                ctext = {c["id"]: c["text"] for c in claims.chunks(lib, lib.get(key))}
                row["grounded"] = sum(claims.grounded(m.get("quote", ""), ctext.get(m.get("chunk"), ""))
                                      for m in ms)
                row["passages_valid"] = sum(m.get("chunk") in ctext for m in ms)
                row["calls_at_cap"] = sum(len((o or {}).get("mentions") or []) >= claims.MAX_ITEMS
                                          for o in r["parts"])
                row["_table"] = out.get("runs") or []
                # labelled by the rule the judge used, so the two are compared like for like
                rule = JUDGE_EVIDENCE_RULE.get(jver.get(jid), 1) if jid in jver else claims.EVIDENCE_RULE
                evs = [claims.evidence_type(m, out.get("runs"), rule=rule) if "evidence" in m
                       else m.get("evidence_type") for m in ms]
                row["evidence_rule"] = rule
                row["evidence_unknown"] = sum(e == "unknown" for e in evs)
                now = [claims.evidence_type(m, out.get("runs")) for m in ms if "evidence" in m]
                row["evidence_unknown_now"] = sum(e == "unknown" for e in now)       # under the current rule
                row["evidence_inferred"] = sum(claims.evidence_inferred(m, out.get("runs")) for m in ms)
                row["model_labels"] = [{"claim_type": m.get("claim_type"), "evidence_type": e,
                                        "hedging": m.get("hedging")} for m, e in zip(ms, evs)]
            else:
                cl = out.get("claims") or []
                row["claims"] = len(cl)
                row.update(_copy_row([c.get("claim", "") for c in cl], grams, lib.get(key).title))
                row["passages_valid"] = sum(bool(c.get("passages")) and all(p in r["passages"] for p in c["passages"])
                                            for c in cl)
        jd = jud.get(jid)
        if jd:
            cov = jd.get("key_points_covered")
            covered = set(cov) if isinstance(cov, list) else {k for k, v in (cov or {}).items() if v}
            row["core_hits"] = sorted(covered & core)
            row["kp_hits"] = sorted(covered & {k["id"] for k in kps})
            row["core_covered"] = len(covered & core)
            row["core"] = len(core)
            row["kp_covered"] = len(covered & {k["id"] for k in kps})
            row["kp"] = len(kps)
            row["_core_ids"] = sorted(core)
            row["_all_ids"] = sorted(k["id"] for k in kps)
            items = jd.get("statements") if task == "summary" else jd.get("claims")
            row["verdicts"] = dict(Counter(x.get("verdict") for x in items or []))
            if task == "summary" and "items" in jd:
                ji = jd.get("items") or []
                row["item_verdicts"] = dict(Counter(x.get("verdict") for x in ji))
                labels = row.get("item_labels") or []
                for f in ("kind", "hedging"):
                    row[f"{f}_pairs"] = [[labels[x["n"] - 1][f], x.get(f)] for x in ji
                                         if isinstance(x.get("n"), int) and 0 < x["n"] <= len(labels)]
                cov_all = set(jd.get("key_points_covered_all") or []) | covered
                row["core_covered_all"] = len(cov_all & core)
                row["kp_covered_all"] = len(cov_all & {k["id"] for k in kps})
            if task == "mentions":
                labels = row.get("model_labels") or []
                for f in ("claim_type", "evidence_type", "hedging"):
                    row[f"{f}_pairs"] = [[labels[x["n"] - 1][f], x.get(f)] for x in items or []
                                         if isinstance(x.get("n"), int) and 0 < x["n"] <= len(labels)]
                row["standalone"] = sum(bool(x.get("standalone")) for x in items or [])
                row["cites_ok"] = dict(Counter(x.get("cites_ok") for x in items or []
                                               if x.get("cites_ok") not in (None, "not_given")))
                table = row.pop("_table", None) or []
                if "runs" in jd:                   # v4: the judge graded the experiments table
                    row["run_verdicts"] = dict(Counter(x.get("verdict") for x in jd.get("runs") or []))
                    row["run_wrong_fields"] = dict(Counter(f
                                                           for x in jd.get("runs") or [] for f in x.get("wrong") or []))
                    row["runs_missed"] = len(jd.get("runs_missed") or [])
                    row["run_verdicts_by_kind"] = dict(Counter(
                        f"{(table[x['n'] - 1].get('kind') or 'model_run') if isinstance(x.get('n'), int) and 0 < x['n'] <= len(table) else '?'}:"
                        f"{x.get('verdict')}" for x in jd.get("runs") or []))
            if task == "extract":
                row["strength_ok"] = sum(bool(x.get("strength_ok")) for x in items or [])
                row["standalone"] = sum(bool(x.get("standalone")) for x in items or [])
        rows.append(row)
    s = summarize(task, rows)
    s["by_length"] = {f"<={LONG_PAPER // 1000}k": _brief(task, [r for r in rows if r["paper_chars"] <= LONG_PAPER]),
                      f">{LONG_PAPER // 1000}k": _brief(task, [r for r in rows if r["paper_chars"] > LONG_PAPER])}
    return {"meta": meta, "rows": rows, "summary": s}


def _copy_row(texts: list[str], grams: set, title: str) -> dict:
    """Copying in a list of statements: the longest run, how many reach
    COPY_LIMIT, and the share of 6-word sequences found in the paper (logged)."""
    runs, shared, total = [], 0, 0
    for t in texts:
        b, _, s_, n = derived.longest_copy(t, grams, title=title)
        runs.append(b)
        shared, total = shared + s_, total + n
    return {"copied_run": max(runs, default=0), "copied_12": sum(x >= derived.COPY_LIMIT for x in runs),
            "shared_6grams": round(shared / total, 3) if total else 0.0}


def _brief(task, rows) -> dict:
    s = summarize(task, rows)
    return {k: s.get(k) for k in ("papers", "judged", "precision", "precision_ci", "core_coverage",
                                  "core_coverage_ci")}


def _good(task) -> str:
    return "supported" if task == "summary" else "correct"


def compare(task: str, a_rows: list[dict], b_rows: list[dict]) -> dict:
    """Two runs on the same papers, item by item: McNemar on the key points
    each covers, and a paired bootstrap (over papers) of the precision gap."""
    def hits(rows, field, total):
        out = {}
        for r in rows:
            if r.get("judged"):
                got = set(r.get(field, []))
                for k in r.get(f"_{total}", []):
                    out[(r["key"], k)] = k in got
        return out

    def prec(rows):
        g = _good(task)
        return {r["key"]: (r["verdicts"].get(g, 0), sum(r["verdicts"].values())) for r in rows if r.get("judged")}
    res = {}
    for name, field in (("core", "core_hits"), ("all", "kp_hits")):
        pa, pb = hits(a_rows, field, name + "_ids"), hits(b_rows, field, name + "_ids")
        p = evalstats.paired(pa, pb)
        p["a_only_ids"] = [f"{k}:{i}" for k, i in p["a_only_ids"]]
        p["b_only_ids"] = [f"{k}:{i}" for k, i in p["b_only_ids"]]
        res[f"key_points_{name}"] = p
    res["precision_diff"] = evalstats.bootstrap_diff(prec(a_rows), prec(b_rows))
    return res


def summarize(task, rows) -> dict:
    j = [r for r in rows if r.get("judged")]
    v = Counter()
    for r in j:
        v.update(r["verdicts"])
    n_items = sum(v.values())
    good = "supported" if task == "summary" else "correct"
    s = {"papers": len(rows), "outputs": sum(r["ok"] for r in rows), "judged": len(j),
         "verdicts": dict(v), "precision": round(v[good] / n_items, 3) if n_items else None,
         "core_coverage": round(sum(r["core_covered"] for r in j) / max(1, sum(r["core"] for r in j)), 3) if j else None,
         "kp_coverage": round(sum(r["kp_covered"] for r in j) / max(1, sum(r["kp"] for r in j)), 3) if j else None,
         "copied_run_max": max((r.get("copied_run", 0) for r in rows if r["ok"]), default=0),
         "median_seconds": sorted(r["seconds"] for r in rows if r.get("seconds"))[len([r for r in rows if r.get("seconds")]) // 2]
         if any(r.get("seconds") for r in rows) else None}
    s["precision_ci"] = evalstats.bootstrap_ratio(
        [(r["verdicts"].get(good, 0), sum(r["verdicts"].values())) for r in j])
    s["core_coverage_ci"] = evalstats.bootstrap_ratio([(r["core_covered"], r["core"]) for r in j])
    if task == "summary" and any("item_verdicts" in r for r in j):
        ji = [r for r in j if "item_verdicts" in r]
        iv = Counter()
        for r in ji:
            iv.update(r["item_verdicts"])
        ni = sum(iv.values())
        n_items = sum(r.get("items", 0) for r in rows)
        s.update({
            "words_median": sorted(r["words"] for r in rows if "words" in r)[len([r for r in rows if "words" in r]) // 2]
            if any("words" in r for r in rows) else None,
            "items": n_items, "items_per_paper": round(n_items / max(1, s["outputs"]), 1),
            "items_located": round(sum(r.get("items_located", 0) for r in rows) / max(1, n_items), 3),
            "item_verdicts": dict(iv), "item_precision": round(iv["correct"] / ni, 3) if ni else None,
            "item_precision_ci": evalstats.bootstrap_ratio(
                [(r["item_verdicts"].get("correct", 0), sum(r["item_verdicts"].values())) for r in ji]),
            "core_coverage_all": round(sum(r["core_covered_all"] for r in ji) / max(1, sum(r["core"] for r in ji)), 3),
            "core_coverage_all_ci": evalstats.bootstrap_ratio([(r["core_covered_all"], r["core"]) for r in ji]),
            "kp_coverage_all": round(sum(r["kp_covered_all"] for r in ji) / max(1, sum(r["kp"] for r in ji)), 3)})
        for f in ("kind", "hedging"):
            pairs = [tuple(x) for r in ji for x in r.get(f"{f}_pairs", [])]
            s[f"item_{f}_accuracy"] = round(sum(a == b for a, b in pairs) / len(pairs), 3) if pairs else None
    ok_rows = [r for r in rows if r["ok"]]
    s["shared_6grams_mean"] = round(sum(r.get("shared_6grams", 0) for r in ok_rows) / max(1, len(ok_rows)), 3)
    s["reasked_calls"] = sum(r.get("reasked", 0) for r in rows)
    if task == "mentions":
        n = sum(r.get("claims", 0) for r in rows)
        s["copied_12_first"] = sum(r.get("copied_12_first", 0) for r in rows)
        s["rewrites"] = {k: sum((r.get("rewrites") or {}).get(k, 0) for r in rows)
                         for k in ("asked", "candidates", "used")}
        s.update({"claims": n, "claims_per_paper": round(n / max(1, s["outputs"]), 1),
                  "copied_12": sum(r.get("copied_12", 0) for r in rows),
                  "grounded": round(sum(r.get("grounded", 0) for r in rows) / max(1, n), 3),
                  "standalone": round(sum(r.get("standalone", 0) for r in j) / max(1, n_items), 3) if j else None})
        for f in ("claim_type", "evidence_type", "hedging"):
            pairs = [tuple(x) for r in j for x in r.get(f"{f}_pairs", [])]
            ok = sum(a == b for a, b in pairs)
            s[f"{f}_accuracy"] = round(ok / len(pairs), 3) if pairs else None
            s[f"{f}_accuracy_ci"] = evalstats.bootstrap_ratio(
                [(sum(a == b for a, b in r.get(f"{f}_pairs", [])), len(r.get(f"{f}_pairs", []))) for r in j])
            s[f"{f}_confusion"] = dict(Counter(f"{b}->{a}" for a, b in pairs if a != b))   # truth->model
        s["calls"] = sum(r["calls"] for r in rows)
        s["calls_at_cap"] = sum(r.get("calls_at_cap", 0) for r in rows)
        s["evidence_unknown"] = round(sum(r.get("evidence_unknown", 0) for r in rows) / max(1, n), 3)
        s["evidence_rule"] = sorted({r["evidence_rule"] for r in rows if "evidence_rule" in r})
        s[f"evidence_unknown_rule{claims.EVIDENCE_RULE}"] = round(sum(r.get("evidence_unknown_now", 0) for r in rows)
                                                              / max(1, n), 3)
        s["evidence_inferred"] = round(sum(r.get("evidence_inferred", 0) for r in rows) / max(1, n), 3)
        ev = [tuple(x) for r in j for x in r.get("evidence_type_pairs", [])]
        s["evidence_unknown_judge"] = round(sum(b == "unknown" for _, b in ev) / len(ev), 3) if ev else None
        ck = sum((Counter(r.get("cites_ok") or {}) for r in j), Counter())
        if ck:                                   # v6: citations on mentions
            judged = sum(ck.values())
            s["cites"] = {"judged": judged, **dict(ck),
                          "accuracy": round((ck["right"] + ck["none_needed"]) / judged, 3)}
        jr = [r for r in j if "run_verdicts" in r]
        if jr:                                   # the runs table (v4)
            tot = Counter()
            for r in jr:
                tot.update(r["run_verdicts"])
            found = sum(tot.values())
            s["runs"] = {"found": found, "per_paper": round(found / len(jr), 1),
                         "correct": round(tot.get("correct", 0) / found, 3) if found else None,
                         "verdicts": dict(tot), "missed": sum(r["runs_missed"] for r in jr),
                         "wrong_fields": dict(sum((Counter(r["run_wrong_fields"]) for r in jr), Counter()).most_common(12)),
                         "by_kind": dict(sum((Counter(r.get("run_verdicts_by_kind") or {}) for r in jr), Counter()))}
            s["runs"]["recall"] = round(found / max(1, found + s["runs"]["missed"]), 3)
        ct = [tuple(x) for r in j for x in r.get("claim_type_pairs", [])]
        s["restated_as_shown"] = sum(a == "shown" and b == "restated" for a, b in ct)
        s["restated_total"] = sum(b == "restated" for _, b in ct)
    if task == "extract":
        n = sum(r.get("claims", 0) for r in rows)
        s.update({"claims": n, "claims_per_paper": round(n / max(1, s["outputs"]), 1),
                  "copied_12": sum(r.get("copied_12", 0) for r in rows),
                  "passages_valid": round(sum(r.get("passages_valid", 0) for r in rows) / max(1, n), 3),
                  "strength_ok": round(sum(r.get("strength_ok", 0) for r in j) / max(1, n_items), 3) if j else None,
                  "standalone": round(sum(r.get("standalone", 0) for r in j) / max(1, n_items), 3) if j else None})
    return s


# ---- the registry of runs (eval/runs.jsonl, tracked): every task, model and judge

def record(lib, run_dir: Path, scores: dict, *, registry: Path | None = None, judge_with: str = "claude") -> Path:
    """One row a run and judge (a run judged by Claude and by Gemini has two).
    A run not fully judged is not recorded: its scores would stand for a run
    they cover only part of."""
    if scores.get("judged") is not None and scores.get("judged", 0) < scores.get("outputs", 0):
        raise ValueError(f"{run_dir.name}: only {scores.get('judged')} of {scores.get('outputs')} outputs judged "
                         f"({judge_with}); judge the rest first, then report")
    reg = registry or lib.root / "eval" / "runs.jsonl"
    meta = json.loads((run_dir / "run.json").read_text())
    if judge_with != "claude":
        meta["judge"] = (meta.get("judges") or {}).get(judge_with)
    rel = str(run_dir.relative_to(lib.root)) if run_dir.is_relative_to(lib.root) else str(run_dir)
    row = {"run": rel, "task": meta.get("task"), "eval": meta.get("eval"), "eval_sha256": meta.get("eval_sha256"),
           "date": meta.get("created", "")[:10], "model": meta.get("model"), "via": meta.get("via"),
           "model_info": meta.get("model_info"), "prompt_version": meta.get("prompt_version"),
           "judge": meta.get("judge"), "scores": scores}
    old = load(reg) if reg.exists() else []

    def same(r):
        return r["run"] == rel and _judge_name(r.get("judge")) == _judge_name(row["judge"])
    prev = next((r for r in old if same(r)), {})
    for k in ("model", "model_info"):              # details added by hand to an earlier row survive a re-report
        row[k] = row[k] or prev.get(k)
    rows = [r for r in old if not same(r)]
    rows.append(row)
    reg.parent.mkdir(parents=True, exist_ok=True)
    save(reg, rows)
    return reg


def _judge_name(j: dict | None) -> str:
    m = str((j or {}).get("model") or "")
    return "gemini" if m.startswith("gemini") else ("claude" if m else "")


# ---- the two judges against each other

def _kappa(pairs: list[tuple]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    cats = {x for p in pairs for x in p}
    pe = sum((sum(a == c for a, _ in pairs) / n) * (sum(b == c for _, b in pairs) / n) for c in cats)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


def agree(lib, eval_path: Path, run_dir: Path, a: str = "claude", b: str = "gemini") -> dict:
    """How often two judges agree on one run: key points covered (every task),
    and, for claim lists, each claim's verdict and labels (matched by number)."""
    ra, rb = report(lib, eval_path, run_dir, a), report(lib, eval_path, run_dir, b)
    task = ra["meta"]["task"]
    ja = {r["id"]: r["judgment"] for r in load(judgments_path(run_dir, a))}
    jb = {r["id"]: r["judgment"] for r in load(judgments_path(run_dir, b))}
    both = sorted(set(ja) & set(jb))
    rows_a = {r["id"]: r for r in ra["rows"]}
    rows_b = {r["id"]: r for r in rb["rows"]}
    kp = [(k in set(rows_a[i].get("kp_hits", [])), k in set(rows_b[i].get("kp_hits", [])))
          for i in both for k in rows_a[i].get("_all_ids", [])]
    out = {"task": task, "papers": len(both), "judges": [a, b],
           "key_points": {"n": len(kp), "agree": round(sum(x == y for x, y in kp) / len(kp), 3) if kp else None,
                          "kappa": _kappa(kp)},
           "precision": {a: ra["summary"]["precision"], b: rb["summary"]["precision"]},
           "core_coverage": {a: ra["summary"]["core_coverage"], b: rb["summary"]["core_coverage"]}}
    if task != "summary":
        fields = ["verdict"] + (["claim_type", "evidence_type", "hedging"] if task == "mentions"
                                else ["strength_ok"])
        for f in fields:
            pairs = []
            for i in both:
                xa = {x.get("n"): x.get(f) for x in ja[i].get("claims") or []}
                xb = {x.get("n"): x.get(f) for x in jb[i].get("claims") or []}
                pairs += [(xa[n], xb[n]) for n in sorted(set(xa) & set(xb), key=str)]
            out[f] = {"n": len(pairs), "agree": round(sum(x == y for x, y in pairs) / len(pairs), 3)
                      if pairs else None, "kappa": _kappa(pairs),
                      "confusion": dict(Counter(f"{x}->{y}" for x, y in pairs if x != y))}
    return out
