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
import time
import urllib.error
import urllib.request
from pathlib import Path

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
    try:
        text, real = chat(url, model, job["messages"], schema=job.get("schema"), **kw)
        out = parse_json(text) if job.get("schema") else text
        return {"id": job["id"], "ok": True, "output": out, "model": real}
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        return {"id": job["id"], "ok": False, "error": f"{type(e).__name__}: {e}"[:500], "model": model}


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


def run(jobs: Path, results: Path, url: str, model: str, *, limit_s: float | None = None,
        log=print, **kw) -> int:
    """Run every job not already in results. Stops cleanly before limit_s
    seconds (a SLURM time limit) so a rerun can pick up."""
    seen = done_ids(results)
    todo = [j for j in (json.loads(l) for l in jobs.read_text("utf-8").splitlines() if l.strip())
            if j["id"] not in seen]
    t0, n = time.time(), 0
    with open(results, "a", encoding="utf-8") as f:
        for i, job in enumerate(todo, 1):
            if limit_s and time.time() - t0 > limit_s:
                log(f"time limit: {len(todo) - i + 1} job(s) left; rerun to continue")
                break
            r = run_job(job, url, model, **kw)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            n += r["ok"]
            log(f"[{i}/{len(todo)}] {job['id']} {job.get('kind', '')} "
                + ("ok" if r["ok"] else f"FAILED {r['error'][:120]}"))
    return n


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("jobs")
    p.add_argument("results")
    p.add_argument("--url", required=True, help="OpenAI-compatible base URL, e.g. http://localhost:8000/v1")
    p.add_argument("--model", required=True)
    p.add_argument("--timeout", type=float, default=600)
    p.add_argument("--limit-minutes", type=float, help="stop starting new jobs after this long")
    a = p.parse_args(argv)
    n = run(Path(a.jobs), Path(a.results), a.url, a.model, timeout=a.timeout,
            limit_s=a.limit_minutes * 60 if a.limit_minutes else None)
    print(f"{n} job(s) done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
