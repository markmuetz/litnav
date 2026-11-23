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
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import batch_runner, claims, derived, evalstats, localmodel
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
                "messages": j["messages"], "chars_sent": j["chars_sent"], "passages": []}
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
    return run_dir


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
    "keywords_ok": {"type": "boolean"}}, "required": ["statements", "key_points_covered", "keywords_ok"]}
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
        "note": {"type": "string"}},
        "required": ["n", "verdict", "claim_type", "evidence_type", "hedging", "standalone", "note"]}},
    "key_points_covered": {"type": "object", "additionalProperties": {"type": "array",
                                                                       "items": {"type": "integer"}}}},
    "required": ["claims", "key_points_covered"]}
JUDGE_SCHEMA = {"summary": SUMMARY_JUDGE, "extract": EXTRACT_JUDGE, "mentions": MENTIONS_JUDGE}
JUDGE_VERSION = {"summary": 1, "extract": 1, "mentions": 2}


def _mention_line(n, m) -> str:
    c = m.get("conditions") or {}
    e = m.get("experiment") or {}
    v = m.get("value") or {}
    bits = [f"chunk {m.get('chunk')}", f"source {m.get('source')}", f"claim_type {m.get('claim_type')}",
            f"evidence_type {m.get('evidence_type')}",
            f"hedging {m.get('hedging')}" + (f" ('{m['hedge_words']}')" if m.get("hedge_words") else "")]
    cond = "; ".join(f"{k} {x}" for k, x in {**e, **c}.items() if x)
    if cond:
        bits.append(cond)
    if str(v.get("value", "")).strip():
        bits.append(f"value {v['value']} {v.get('units', '')} [{v.get('low', '')}, {v.get('high', '')}]")
    return f"{n}. {m.get('statement')} [{'; '.join(bits)}]"


def judge_prompt(task, it, text, kps, out) -> str:
    kp = "\n".join(f"{k['id']}{' (core)' if k.get('core') else ''}: {k['point']}" for k in kps)
    head = f"Paper: {it.title} ({it.year})\n\nThe paper's key points (reference):\n{kp}\n\n"
    if task == "summary":
        body = (f"Model's summary:\nTopic: {out.get('topic')}\nSummary: {out.get('summary')}\n"
                f"Keywords: {', '.join(out.get('keywords') or [])}\n\n"
                "1. Split the topic and summary into their factual statements. Give each a verdict: "
                "supported (the paper says it), overstated (more certain or general than the paper), wrong "
                "(a number, direction, method or condition differs), unsupported (not in the paper).\n"
                "2. key_points_covered: the ids of the key points the summary substantially conveys.\n"
                "3. keywords_ok: are the keywords topical and lower-case?\n")
    elif task == "mentions":
        lines = "\n".join(_mention_line(n, m) for n, m in enumerate(out.get("mentions") or [], 1))
        body = (f"Model's extracted claims (mentions), each with its labels:\n{lines}\n\n"
                "1. For each claim n give a verdict on its statement: correct; wrong_detail (a number, unit, "
                "direction, method or condition differs from the paper); overstated (stronger or more general "
                "than the paper); not_in_paper; trivial (true but says nothing useful). A claim the paper "
                "takes from other work is fine if the paper says it; judge its label below.\n"
                "   Then give the right labels, from the paper, whatever the model said:\n"
                "   claim_type: shown (this paper's own results demonstrate it); restated (the paper attributes "
                "it to other work, usually with a citation, or states it as known background); assumed (a "
                "premise or method choice taken as given); speculation (offered without support here). The paper's "
                "own result compared with cited work is shown.\n"
                "   evidence_type: observations; reanalysis; les_crm (large-eddy or cloud-resolving, limited "
                "area); km_scale (convection-permitting regional or global); parametrized_model (GCM or RCM); "
                "theory; mixed; unknown (the paper doesn't say).\n"
                "   hedging: strong (shows, demonstrates); moderate (suggests, indicates, likely); weak (may, "
                "could, possibly): how firmly the paper states it.\n"
                "   standalone: is the statement understandable without the paper?\n"
                "2. key_points_covered: for each key point id, the numbers of the claims that convey it "
                "(an empty list if none).\n")
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
    out = {}
    for j in jobs:
        r = res.get(j["id"], {})
        pid = f"{task}-{j['key']}" if task == "mentions" else j["id"]
        p = out.setdefault(pid, {"id": pid, "key": j["key"], "ok": True, "seconds": 0.0, "passages": [],
                                 "chars_sent": 0, "parts": []})
        good = bool(r.get("ok")) and isinstance(r.get("output"), dict)
        p["ok"] = p["ok"] and good
        p["seconds"] = round(p["seconds"] + (r.get("seconds") or 0), 1) if r else p["seconds"]
        p["passages"] += j.get("passages", [])
        p["chars_sent"] += j.get("chars_sent") or 0
        p["parts"].append(r.get("output") if good else None)
    for p in out.values():
        if task == "mentions":
            p["output"] = {"mentions": [m for o in p["parts"] if o for m in o.get("mentions") or []]} \
                if p["ok"] else None
        else:
            p["output"] = p["parts"][0] if p["ok"] else None
        if not any(p["parts"]):
            p["seconds"] = None
    return out


def judge(lib, eval_path: Path, run_dir: Path, *, model="opus", jobs=4, log=print) -> int:
    meta = json.loads((run_dir / "run.json").read_text())
    task = meta["task"]
    cases = {r["key"]: r for r in load(eval_path)}
    jpath = run_dir / "judgments.jsonl"
    have = {r["id"] for r in load(jpath)} if jpath.exists() else set()
    todo = [r for r in paper_results(run_dir, task).values() if r["ok"] and r["id"] not in have]

    def one(r):
        key = r["key"]
        it = lib.get(key)
        text = it.text()[:JUDGE_CHARS]
        try:
            out, real = _claude(lib, it, JUDGE_SYSTEM, judge_prompt(task, it, text, cases[key]["key_points"],
                                                                     r["output"]),
                                JUDGE_SCHEMA[task], model)
        except Exception as e:                       # noqa: BLE001 - record, go on
            log(f"judge {r['id']}: {type(e).__name__}: {e}")
            return None
        log(f"judged {r['id']}")
        return {"id": r["id"], "key": key, "judge_model": real, "judge_prompt_version": JUDGE_VERSION[task],
                "judgment": out}
    with ThreadPoolExecutor(jobs) as ex:
        done = [x for x in ex.map(one, todo) if x]
    with open(jpath, "a", encoding="utf-8") as f:
        for d in done:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    if done:
        write_run_meta(run_dir, judge={"model": done[0]["judge_model"], "prompt_version": JUDGE_VERSION[task]})
    return len(done)


# ---- scoring

def copied_run(out_text: str, paper_words: set, n: int = 6) -> int:
    """Longest run of words copied from the paper (0 if under n)."""
    w = re.findall(r"[a-z0-9]+", out_text.lower())
    best = run = 0
    for i in range(len(w) - n + 1):
        if " ".join(w[i:i + n]) in paper_words:
            run = run + 1 if run else n
            best = max(best, run)
        else:
            run = 0
    return best


def _ngrams(text, n=6):
    w = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def report(lib, eval_path: Path, run_dir: Path) -> dict:
    meta = json.loads((run_dir / "run.json").read_text())
    task = meta["task"]
    cases = {r["key"]: r for r in load(eval_path)}
    jud = {r["id"]: r["judgment"] for r in load(run_dir / "judgments.jsonl")} \
        if (run_dir / "judgments.jsonl").exists() else {}
    rows = []
    for jid, r in paper_results(run_dir, task).items():
        key = r["key"]
        out = r["output"]
        kps = cases[key].get("key_points", [])
        core = {k["id"] for k in kps if k.get("core")}
        text = lib.get(key).text()
        row = {"id": jid, "key": key, "ok": out is not None, "seconds": r.get("seconds"), "judged": jid in jud,
               "paper_chars": len(text), "chars_sent": r["chars_sent"], "calls": len(r["parts"])}
        if out is not None:
            grams = _ngrams(text)
            if task == "summary":
                row["copied_run"] = copied_run(f"{out.get('topic', '')} {out.get('summary', '')}", grams)
                row["sentences"] = len(re.findall(r"[.!?](\s|$)", out.get("summary") or ""))
            elif task == "mentions":
                ms = out.get("mentions") or []
                row["claims"] = len(ms)
                runs = [copied_run(m.get("statement", ""), grams) for m in ms]
                row["copied_run"] = max(runs, default=0)
                row["copied_12"] = sum(x >= 12 for x in runs)
                ctext = {c["id"]: c["text"] for c in claims.chunks(lib, lib.get(key))}
                row["grounded"] = sum(claims.grounded(m.get("quote", ""), ctext.get(m.get("chunk"), ""))
                                      for m in ms)
                row["passages_valid"] = sum(m.get("chunk") in ctext for m in ms)
                row["calls_at_cap"] = sum(len((o or {}).get("mentions") or []) >= claims.MAX_ITEMS
                                          for o in r["parts"])
                row["evidence_unknown"] = sum(m.get("evidence_type") == "unknown" for m in ms)
                row["model_labels"] = [{f: m.get(f) for f in ("claim_type", "evidence_type", "hedging")}
                                       for m in ms]
            else:
                cl = out.get("claims") or []
                row["claims"] = len(cl)
                runs = [copied_run(c.get("claim", ""), grams) for c in cl]
                row["copied_run"] = max(runs, default=0)
                row["copied_12"] = sum(x >= 12 for x in runs)
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
            if task == "mentions":
                labels = row.get("model_labels") or []
                for f in ("claim_type", "evidence_type", "hedging"):
                    row[f"{f}_pairs"] = [[labels[x["n"] - 1][f], x.get(f)] for x in items or []
                                         if isinstance(x.get("n"), int) and 0 < x["n"] <= len(labels)]
                row["standalone"] = sum(bool(x.get("standalone")) for x in items or [])
            if task == "extract":
                row["strength_ok"] = sum(bool(x.get("strength_ok")) for x in items or [])
                row["standalone"] = sum(bool(x.get("standalone")) for x in items or [])
        rows.append(row)
    s = summarize(task, rows)
    s["by_length"] = {f"<={LONG_PAPER // 1000}k": _brief(task, [r for r in rows if r["paper_chars"] <= LONG_PAPER]),
                      f">{LONG_PAPER // 1000}k": _brief(task, [r for r in rows if r["paper_chars"] > LONG_PAPER])}
    return {"meta": meta, "rows": rows, "summary": s}


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
    if task == "mentions":
        n = sum(r.get("claims", 0) for r in rows)
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
        ev = [tuple(x) for r in j for x in r.get("evidence_type_pairs", [])]
        s["evidence_unknown_judge"] = round(sum(b == "unknown" for _, b in ev) / len(ev), 3) if ev else None
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

def record(lib, run_dir: Path, scores: dict, *, registry: Path | None = None) -> Path:
    reg = registry or lib.root / "eval" / "runs.jsonl"
    meta = json.loads((run_dir / "run.json").read_text())
    rel = str(run_dir.relative_to(lib.root)) if run_dir.is_relative_to(lib.root) else str(run_dir)
    row = {"run": rel, "task": meta.get("task"), "eval": meta.get("eval"), "eval_sha256": meta.get("eval_sha256"),
           "date": meta.get("created", "")[:10], "model": meta.get("model"), "via": meta.get("via"),
           "model_info": meta.get("model_info"), "prompt_version": meta.get("prompt_version"),
           "judge": meta.get("judge"), "scores": scores}
    old = load(reg) if reg.exists() else []
    prev = next((r for r in old if r["run"] == rel), {})
    for k in ("model", "model_info"):              # details added by hand to an earlier row survive a re-report
        row[k] = row[k] or prev.get(k)
    rows = [r for r in old if r["run"] != rel]
    rows.append(row)
    reg.parent.mkdir(parents=True, exist_ok=True)
    save(reg, rows)
    return reg
