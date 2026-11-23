#!/usr/bin/env python3
"""Work through a spool of litnav model batches on a cluster node.

Standard library only (Python 3.9+), copied into the spool by
`litnav queue push`, and run by the GPU job once its model server is up:

    python3 SPOOL/run_spool.py SPOOL --url http://127.0.0.1:PORT/v1 --model M \
        --api-key-file ~/.config/x/token --concurrency 48 --limit-minutes 1380

The spool:
    inbox/<batch>/jobs.jsonl     pushed from the laptop (copied in as <batch>.part, then renamed)
    running/<batch>/             being worked on; results.jsonl grows as jobs finish
    done/<batch>/                every job has a result; `litnav queue pull` fetches and deletes it
    status.json                  what the worker is doing (read by `litnav queue status`)

Batches left in running/ by a killed job are taken first, and resume where
they stopped. Exit status: 0 when the spool is empty, 3 when the time limit
stopped it with work left (the job script can resubmit itself), 1 on error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

try:                                   # inside litnav
    from . import batch_runner as run_jobs
except ImportError:                    # copied into the spool beside run_jobs.py (litnav's batch_runner)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import run_jobs  # noqa: E402


def batches(spool: Path) -> list:
    """Work to do: running/ first (an interrupted job), then inbox/, oldest first."""
    out = []
    for sub in ("running", "inbox"):
        d = spool / sub
        if d.is_dir():
            out += sorted(p for p in d.iterdir() if p.is_dir() and not p.name.endswith(".part"))
    return out


def write_status(spool: Path, **kw) -> None:
    s = {"time": dt.datetime.now().isoformat(timespec="seconds"), "host": socket.gethostname(),
         "job": os.environ.get("SLURM_JOB_ID", ""), **kw}
    tmp = spool / "status.json.tmp"
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(spool / "status.json")


def server_info(url: str, model: str, api_key: str = "") -> dict:
    """What served the batch: the model as the server reports it, the node and its GPUs."""
    import subprocess
    import urllib.request
    info = {"model": model, "machine": socket.gethostname().split(".")[0],
            "job": os.environ.get("SLURM_JOB_ID", ""), "server": "openai-compatible"}
    try:
        req = urllib.request.Request(url.rstrip("/") + "/models",
                                     headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
        with urllib.request.urlopen(req, timeout=20) as r:
            for m in json.load(r).get("data", []):
                if m.get("id") == model:
                    info.update({k: m[k] for k in ("max_model_len", "root", "owned_by") if k in m})
                    info["server"] = "vllm" if m.get("owned_by") == "vllm" else info["server"]
    except Exception as e:                       # noqa: BLE001
        info["models_error"] = type(e).__name__
    try:
        g = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=20).stdout.split("\n")
        g = [x.strip() for x in g if x.strip()]
        if g:
            info["gpus"] = f"{len(g)} x {g[0]}"
    except Exception:                            # noqa: BLE001
        pass
    for k in ("LLM_MAX_NUM_SEQS", "LLM_MAX_LEN"):
        if os.environ.get(k):
            info[k.lower()] = os.environ[k]
    return info


def work(spool: Path, url: str, model: str, *, limit_s: float | None, concurrency: int,
         log=print, **kw) -> int:
    t0 = time.time()
    server = dict(server_info(url, model, kw.get("api_key", "")), concurrency=concurrency)
    for sub in ("running", "done"):
        (spool / sub).mkdir(parents=True, exist_ok=True)
    while True:
        todo = batches(spool)
        if not todo:
            write_status(spool, state="idle", model=model, note="spool empty: job ending")
            log("spool empty")
            return 0
        b = todo[0]
        if b.parent.name == "inbox":
            dest = spool / "running" / b.name
            b.rename(dest)
            b = dest
        left_s = None if limit_s is None else limit_s - (time.time() - t0)
        if left_s is not None and left_s <= 0:
            write_status(spool, state="stopped", model=model, note="time limit; work left")
            return 3
        (b / "server.json").write_text(json.dumps(server, indent=1))
        n_jobs = sum(1 for l in (b / "jobs.jsonl").read_text("utf-8").splitlines() if l.strip())
        write_status(spool, state="running", batch=b.name, jobs=n_jobs, model=model,
                     started=dt.datetime.now().isoformat(timespec="seconds"))
        log(f"batch {b.name}: {n_jobs} job(s)")
        run_jobs.run(b / "jobs.jsonl", b / "results.jsonl", url, model, limit_s=left_s,
                     concurrency=concurrency, log=log, **kw)
        if not run_jobs.todo_jobs(b / "jobs.jsonl", b / "results.jsonl"):
            dest = spool / "done" / b.name
            if dest.exists():
                shutil.rmtree(dest)
            b.rename(dest)
            log(f"batch {b.name} done")
        elif limit_s is not None and time.time() - t0 >= limit_s:
            write_status(spool, state="stopped", batch=b.name, model=model, note="time limit; work left")
            return 3
        else:                      # jobs failed (server errors): leave them for the next job
            write_status(spool, state="stopped", batch=b.name, model=model,
                         note="some jobs failed; left in running/ for the next job")
            log(f"batch {b.name}: some jobs failed; stopping")
            return 3


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("spool")
    p.add_argument("--url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--api-key-file")
    p.add_argument("--concurrency", "-j", type=int, default=32)
    p.add_argument("--timeout", type=float, default=900)
    p.add_argument("--limit-minutes", type=float, help="stop starting jobs after this long")
    a = p.parse_args(argv)
    key = Path(a.api_key_file).expanduser().read_text().strip() if a.api_key_file else ""
    spool = Path(a.spool).expanduser()
    try:
        return work(spool, a.url, a.model, limit_s=a.limit_minutes * 60 if a.limit_minutes else None,
                    concurrency=a.concurrency, timeout=a.timeout, api_key=key,
                    log=lambda m: print(m, flush=True))
    except Exception as e:                         # noqa: BLE001
        write_status(spool, state="error", error=f"{type(e).__name__}: {e}"[:300])
        raise


if __name__ == "__main__":
    sys.exit(main())
