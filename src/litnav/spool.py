"""Batches for a cluster GPU node that is free only some of the time.

`litnav queue push` copies the queued jobs, as one batch, into a spool
directory on the cluster over ssh, and submits the worker job unless one is
already queued or running. The worker (the cluster's own job script,
running `run_spool.py`) starts the model server when SLURM gives it a node,
works through every batch in the spool, including ones pushed while it
waited, and ends when the spool is empty. `litnav queue pull` fetches the
results of finished and partly finished batches, applies the new ones, and
deletes finished batches on both sides. `litnav queue status` shows the
worker's state and the spool.

Configure in .litnav/local.toml:

    [batch_remote]
    host = "sci-host.example.org"          # ssh host (your ssh config does the rest)
    spool = "~/litnav-spool"               # on the cluster; made private to you (chmod 700)
    submit = "sbatch --export=ALL,LLM_SPOOL=$HOME/litnav-spool /path/to/worker.sbatch"
    job_name = "llm-batch"                 # the worker's SLURM job name(s) (to see if one is queued)

A batch holds the text of the papers its jobs need: the spool lives in your
own account, and finished batches are deleted when pulled.
"""
from __future__ import annotations

import datetime as dt
import json
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path

from . import batch_runner, copyfix, localmodel, spool_runner
from .util import atomic_write


class RemoteError(RuntimeError):
    pass


def remote_cfg(lib) -> dict:
    c = dict(lib.config.get("batch_remote", {}))
    if not c.get("host") or not c.get("spool"):
        raise ValueError("set [batch_remote] host and spool in .litnav/local.toml "
                         "(see `litnav queue push --help`)")
    return c


def _ssh(cfg, cmd: str, *, check=True) -> str:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", cfg["host"], cmd],
                       capture_output=True, text=True, timeout=300)
    if check and r.returncode != 0:
        raise RemoteError(f"ssh {cfg['host']}: {r.stderr.strip()[-400:] or r.returncode}")
    return r.stdout


def _rsync(*args):
    r = subprocess.run(["rsync", "-a", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=20", *args],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RemoteError(f"rsync: {r.stderr.strip()[-400:]}")


def _spool(cfg) -> str:
    """The spool path as the remote shell should see it (~ expanded there)."""
    s = cfg["spool"]
    return "$HOME" + s[1:] if s.startswith("~") else shlex.quote(s)


def _rpath(cfg, sub: str) -> str:
    s = cfg["spool"].rstrip("/")
    return f"{cfg['host']}:{s}/{sub}"


# ---- local state

def state_path(lib) -> Path:
    return lib.index_dir / "batches.json"


def load_state(lib) -> dict:
    p = state_path(lib)
    return json.loads(p.read_text("utf-8")) if p.is_file() else {}


def save_state(lib, st: dict) -> None:
    p = state_path(lib)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(st, indent=1))


def local_dir(lib, bid: str) -> Path:
    return lib.root / "derived" / "batches" / bid


def in_flight(lib) -> set:
    return {i for b in load_state(lib).values() if b["state"] != "done" for i in b["jobs"]}


# ---- push

def push(lib, jobs: list | None = None, *, kind: str = "queue", meta: dict | None = None,
         submit: bool = True, log=print) -> dict:
    """Send jobs (default: the queued jobs not already pushed) to the spool as one batch."""
    cfg = remote_cfg(lib)
    if jobs is None:
        busy = in_flight(lib)
        jobs = [j for j in localmodel.queued(lib) if j["id"] not in busy]
    if not jobs:
        return {"batch": None, "jobs": 0}
    for j in jobs:                          # sharing may have changed since queuing
        localmodel.gate(lib.get(j["key"]))
    bid = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    d = local_dir(lib, bid)
    d.mkdir(parents=True)
    atomic_write(d / "jobs.jsonl", "".join(json.dumps(j, ensure_ascii=False) + "\n" for j in jobs))
    (d / "meta.json").write_text(json.dumps({"batch": bid, "kind": kind, "jobs": len(jobs),
                                             "pushed": dt.datetime.now().isoformat(timespec="seconds")}))
    sp = _spool(cfg)
    _ssh(cfg, f"mkdir -p {sp}/inbox {sp}/running {sp}/done {sp}/logs && chmod 700 {sp}")
    _rsync(str(Path(batch_runner.__file__)), _rpath(cfg, "run_jobs.py"))
    _rsync(str(Path(copyfix.__file__)), _rpath(cfg, "copyfix.py"))
    _rsync(str(Path(spool_runner.__file__)), _rpath(cfg, "run_spool.py"))
    _rsync(str(d) + "/", _rpath(cfg, f"inbox/{bid}.part/"))
    _ssh(cfg, f"mv {sp}/inbox/{bid}.part {sp}/inbox/{bid}")
    st = load_state(lib)
    st[bid] = {"state": "pushed", "kind": kind, "pushed": dt.datetime.now().isoformat(timespec="seconds"),
               "jobs": [j["id"] for j in jobs], "imported": [], **(meta or {})}
    save_state(lib, st)
    log(f"pushed batch {bid}: {len(jobs)} job(s)")
    job = submit_worker(lib, cfg, log=log) if submit else None
    return {"batch": bid, "jobs": len(jobs), "worker": job}


def worker_job(cfg) -> str:
    """The queued or running worker job ('id STATE elapsed limit reason'), or ''."""
    name = cfg.get("job_name")
    if not name:
        return ""
    if isinstance(name, list):              # several jobs can work the spool (e.g. a self-test + batch)
        name = ",".join(name)
    return _ssh(cfg, f"squeue -h -u $USER -n {shlex.quote(name)} -o '%i %T %M %l %R' | head -1").strip()


def submit_worker(lib, cfg=None, *, log=print) -> str:
    cfg = cfg or remote_cfg(lib)
    if not cfg.get("submit"):
        log("no [batch_remote] submit command: start the worker yourself")
        return ""
    have = worker_job(cfg)
    if have:
        log(f"worker job already queued or running: {have}")
        return have.split()[0]
    out = _ssh(cfg, cfg["submit"]).strip()
    log(f"submitted the worker: {out}")
    return out.split()[-1] if out else ""


# ---- pull

def _remote_where(cfg, bids) -> dict:
    sp = _spool(cfg)
    out = _ssh(cfg, " ; ".join(f"for s in inbox running done; do [ -d {sp}/$s/{b} ] && echo {b} $s; done"
                               for b in bids) + " ; true")
    return dict(l.split() for l in out.splitlines() if len(l.split()) == 2)


def pull(lib, *, log=print) -> dict:
    """Fetch and apply the new results of every open batch; delete finished ones."""
    cfg = remote_cfg(lib)
    st = load_state(lib)
    open_ = [b for b, v in st.items() if v["state"] != "done"]
    if not open_:
        return {}
    where = _remote_where(cfg, open_)
    summary = {}
    for bid in open_:
        b, d = st[bid], local_dir(lib, bid)
        loc = where.get(bid)
        if loc is None:
            log(f"{bid}: not in the spool (deleted on the cluster?); left as is")
            summary[bid] = "missing"
            continue
        if loc != "inbox":
            _rsync(_rpath(cfg, f"{loc}/{bid}/results.jsonl"), str(d / "results.jsonl"))
            try:
                _rsync(_rpath(cfg, f"{loc}/{bid}/server.json"), str(d / "server.json"))
            except RemoteError:
                pass                       # an older worker
        new = _apply(lib, bid, b, d, log=log, final=loc == "done") if (d / "results.jsonl").is_file() else 0
        b["state"] = {"inbox": "waiting", "running": "running", "done": "finished"}[loc]
        if loc == "done" and set(b["imported"]) >= set(b["jobs"]) - set(b.get("failed", [])):
            _ssh(cfg, f"rm -rf {_spool(cfg)}/done/{bid}")
            shutil.rmtree(d, ignore_errors=True)
            b["state"] = "done"
        summary[bid] = f"{b['state']}: {len(b['imported'])}/{len(b['jobs'])} imported (+{new})"
        save_state(lib, st)
    return summary


def _apply(lib, bid, b, d, *, log, final: bool = True) -> int:
    """final: the batch is finished on the cluster (no more follow-ups to come)."""
    jobs = {j["id"]: j for j in (json.loads(l) for l in (d / "jobs.jsonl").read_text("utf-8").splitlines()
                                 if l.strip())}
    done = set(b["imported"])
    fresh = []
    ok_all = {}
    for line in (d / "results.jsonl").read_text("utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("ok") and r.get("id"):
            ok_all[r["id"]] = r
        rid = r.get("id") or ""
        known = rid in jobs or (rid.endswith(("-r", "-c")) and rid[:-2] in jobs)    # a follow-up run on the node
        if known and rid not in done and r.get("ok"):
            fresh.append(r)
            done.add(r["id"])
    server = json.loads((d / "server.json").read_text()) if (d / "server.json").is_file() else None
    if b["kind"] == "eval":
        from . import modeleval
        modeleval.import_batch(lib, b, jobs, fresh, log=log, server=server)
    elif b["kind"] == "paper-eval":
        from . import papereval
        papereval.import_batch(lib, b, jobs, fresh, server, log=log)
    else:
        applied = set()
        for r in fresh:
            if r["id"] not in jobs:                  # a runner's re-ask: applied with its job
                continue
            job = jobs[r["id"]]
            s = localmodel.apply_or_followup(lib, job, r, ok_all, via="batch")
            if s.get("rephrase") and job.get("followup") and not final:
                done.discard(r["id"])                # the runner may still re-ask it: wait for that
                continue
            if s.get("rephrase"):                   # copied from the paper: the next batch asks again
                localmodel.enqueue(lib, [s["rephrase"]])
                applied.add(r["id"])
                log(f"  {jobs[r['id']]['key']}: {s['error']}; queued again with a note")
            elif s["status"] in ("ok", "stale"):
                applied.add(r["id"])
            else:
                b.setdefault("failed", []).append(r["id"])
                log(f"  {jobs[r['id']]['key']}: {s['status']} ({s.get('error', '')})")
        localmodel._rewrite_queue(lib, [j for j in localmodel.queued(lib) if j["id"] not in applied])
    b["imported"] = sorted(done)
    return len(fresh)


# ---- status

def status(lib) -> dict:
    cfg = remote_cfg(lib)
    sp = _spool(cfg)
    raw = _ssh(cfg, f"cat {sp}/status.json 2>/dev/null || echo '{{}}'; echo '@@'; "
                    f"for s in inbox running done; do for b in {sp}/$s/*/; do [ -d \"$b\" ] || continue; "
                    f"echo $s $(basename $b) $(cat $b/jobs.jsonl 2>/dev/null | wc -l) "
                    f"$(cat $b/results.jsonl 2>/dev/null | wc -l); done; done", check=False)
    head, _, rest = raw.partition("@@")
    try:
        worker = json.loads(head)
    except json.JSONDecodeError:
        worker = {}
    spool = [dict(zip(("where", "batch", "jobs", "results"), l.split())) for l in rest.splitlines()
             if len(l.split()) == 4]
    return {"job": worker_job(cfg), "worker": worker, "spool": spool,
            "local": {b: {k: (len(v) if isinstance(v, list) else v) for k, v in s.items()}
                      for b, s in load_state(lib).items() if s["state"] != "done"}}
