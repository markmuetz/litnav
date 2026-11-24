#!/usr/bin/env python3
"""Run litnav model jobs against an OpenAI-compatible chat endpoint.

Standard library only, so the same file runs on this machine (litnav calls
it for `--via local`) and, copied into an exported batch, on a cluster with
nothing installed: JASMIN/ORCHID, with vLLM or Ollama serving the model.

    python3 run_jobs.py --url http://localhost:8000/v1 --model M jobs.jsonl results.jsonl

jobs.jsonl: one {"id", "kind", "messages", "schema", ...} per line.
results.jsonl: one {"id", "ok", "output" | "error", "model"} per line,
appended as each job finishes; a rerun skips ids already there, so an
interrupted (or time-limited SLURM) run just resumes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import urllib.error
import urllib.request
from pathlib import Path

try:
    from . import copyfix
except ImportError:                    # shipped beside this file as copyfix.py (or missing: no follow-ups)
    try:
        import copyfix  # noqa: E402
    except ImportError:
        copyfix = None

_THINK = re.compile(r"<think>.*?</think>", re.S)


def parse_json(text: str):
    """The first JSON object in a reply: tolerates <think> blocks and fences."""
    t = _THINK.sub("", text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    while start != -1:
        depth, instr, esc = 0, False, False
        for i in range(start, len(t)):
            c = t[i]
            if instr:
                esc = (c == "\\" and not esc)
                if c == '"' and not esc:
                    instr = False
                continue
            if c == '"':
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = t.find("{", start + 1)
    raise ValueError("no JSON object in the reply")


def chat(url: str, model: str, messages: list, *, schema: dict | None = None,
         timeout: float = 600, api_key: str = "", temperature: float = 0.1) -> tuple[str, str]:
    """One chat completion. Returns (reply text, model name the server reports)."""
    body = {"model": model, "messages": messages, "temperature": temperature, "stream": False}
    if schema:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "result", "schema": schema}}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {api_key}"} if api_key else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return d["choices"][0]["message"].get("content") or "", d.get("model") or model


def run_job(job: dict, url: str, model: str, **kw) -> dict:
    t0, text = time.time(), ""
    try:
        text, real = chat(url, model, job["messages"], schema=job.get("schema"), **kw)
        out = parse_json(text) if job.get("schema") else text
        return {"id": job["id"], "ok": True, "output": out, "model": real, "raw": text,
                "seconds": round(time.time() - t0, 1), **_chain_fields(job)}
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        return {"id": job["id"], "ok": False, "error": f"{type(e).__name__}: {e}"[:500], "model": model,
                "raw": text, "seconds": round(time.time() - t0, 1)}


def _chain_fields(job: dict) -> dict:
    """A follow-up's own fields, kept with its result (which statements it rewrote or checked)."""
    return {k: job[k] for k in ("rewrite", "meaning_check") if k in job}


def _base(jid: str) -> str:
    return jid[:-2] if jid.endswith(("-r", "-c")) else jid


def load_results(results: Path) -> dict:
    """The latest successful result by id (a failure only where nothing succeeded)."""
    out = {}
    if results.is_file():
        for line in results.read_text("utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("id") and (r["id"] not in out or r.get("ok")):
                out[r["id"]] = r
    return out


def followup(job: dict, results: dict) -> dict | None:
    """The next follow-up for a job (copyfix.next_job), if the job asks for one."""
    if copyfix is None or not job.get("followup"):
        return None
    return copyfix.next_job(job, results)


def done_ids(results: Path) -> set[str]:
    if not results.is_file():
        return set()
    out = set()
    for line in results.read_text("utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue                      # a line cut off by a killed job
        if r.get("ok"):
            out.add(r["id"])
    return out


def todo_jobs(jobs: Path, results: Path) -> list:
    """Jobs without a result, and the follow-ups due for jobs that have one
    (so a run stopped part-way through a chain picks it up again)."""
    seen = done_ids(results)
    all_ = [json.loads(l) for l in jobs.read_text("utf-8").splitlines() if l.strip()]
    todo = [j for j in all_ if j["id"] not in seen]
    if any(j.get("followup") for j in all_):
        res = load_results(results)
        for j in all_:
            if j["id"] in seen:
                nxt = followup(j, res)
                if nxt and nxt["id"] not in seen:
                    todo.append(nxt)
    return todo


def run(jobs: Path, results: Path, url: str, model: str, *, limit_s: float | None = None,
        concurrency: int = 1, log=print, **kw) -> int:
    """Run every job not already in results, `concurrency` at a time (vLLM
    batches parallel requests; Ollama mostly queues them). Stops starting
    jobs after limit_s seconds (a SLURM time limit) so a rerun can pick up;
    each result is written as it finishes."""
    todo = todo_jobs(jobs, results)
    all_ = [json.loads(l) for l in jobs.read_text("utf-8").splitlines() if l.strip()]
    chained = copyfix is not None and any(j.get("followup") for j in all_)
    by_id = {j["id"]: j for j in all_}        # parents, to chain each follow-up from
    res = load_results(results) if chained else {}
    total = len(todo)
    extra = []                                # follow-ups started as their parent finishes
    if results.is_file() and results.stat().st_size:
        with open(results, "rb") as f:          # a killed job can leave half a line: start a new one
            f.seek(-1, 2)
            if f.read(1) != b"\n":
                with open(results, "a", encoding="utf-8") as g:
                    g.write("\n")
    t0, n, i = time.time(), 0, 0
    lock = threading.Lock()
    with open(results, "a", encoding="utf-8") as f, \
            ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        pending, it = set(), iter(todo)
        stopped = False
        while True:
            while not stopped and len(pending) < max(1, concurrency):
                if limit_s and time.time() - t0 > limit_s:
                    stopped = True
                    left = total - i
                    if left:
                        log(f"time limit: {left} job(s) not started; rerun to continue")
                    break
                job = extra.pop(0) if extra else next(it, None)
                if job is None:
                    if pending:
                        break                 # a running parent may still add a follow-up
                    stopped = True
                    break
                i += 1
                pending.add(pool.submit(run_job, job, url, model, **kw))
            if not pending:
                break
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            for fut in finished:
                r = fut.result()
                with lock:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    f.flush()
                n += r["ok"]
                if chained:
                    res[r["id"]] = r
                    parent = by_id.get(_base(r["id"]))
                    nxt = followup(parent, res) if parent and r["ok"] else None
                    if nxt and not any(x["id"] == nxt["id"] for x in extra):
                        extra.append(nxt)
                        total += 1
                rate = n / max(time.time() - t0, 1) * 3600
                log(f"[{n}/{total}] {r['id']} {r['seconds']:.0f}s "
                    + ("ok" if r["ok"] else f"FAILED {r['error'][:120]}") + f" ({rate:.0f}/h)")
    return n


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("jobs")
    p.add_argument("results")
    p.add_argument("--url", required=True, help="OpenAI-compatible base URL, e.g. http://localhost:8000/v1")
    p.add_argument("--model", required=True)
    p.add_argument("--timeout", type=float, default=600)
    p.add_argument("--limit-minutes", type=float, help="stop starting new jobs after this long")
    p.add_argument("--concurrency", "-j", type=int, default=1, help="requests in flight (vLLM: 32-64)")
    p.add_argument("--api-key-file", help="file holding the server's API key")
    a = p.parse_args(argv)
    key = Path(a.api_key_file).expanduser().read_text().strip() if a.api_key_file else ""
    n = run(Path(a.jobs), Path(a.results), a.url, a.model, timeout=a.timeout, api_key=key,
            concurrency=a.concurrency, limit_s=a.limit_minutes * 60 if a.limit_minutes else None)
    print(f"{n} job(s) done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
