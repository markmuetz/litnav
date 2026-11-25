"""Batches through a spool: push, the worker on the 'cluster', pull. ssh and
rsync are replaced by local commands; the model is a tiny HTTP server."""
import json
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from litnav import localmodel, modeleval, spool

from test_localmodel import ilib, strict  # noqa: F401


class Model(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Model.calls += 1
        ids = body["response_format"]["json_schema"]["schema"]["properties"]["passages"]["items"]["enum"]
        claim = body["messages"][1]["content"].split("\n", 1)[0]
        v = "supported" if "spread" in claim else "contradicted"
        out = {"verdict": v, "confidence": "high", "passages": ids[:1], "explanation": "e", "quote": "",
               "parts": [], "hedging": "matches", "mismatch": ""}
        data = json.dumps({"model": "big-model", "choices": [{"message": {"content": json.dumps(out)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def remote(tmp_path, monkeypatch):
    home = tmp_path / "cluster-home"
    home.mkdir()

    def ssh(cfg, cmd, check=True):
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env={"HOME": str(home),
                           "PATH": "/usr/bin:/bin", "USER": "me"})
        if check and r.returncode:
            raise spool.RemoteError(r.stderr)
        return r.stdout

    def rsync(*args):
        src, dst = [re.sub(r"^cluster:~", str(home), a) for a in args]
        if src.endswith("/"):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copyfile(src, dst)
    monkeypatch.setattr(spool, "_ssh", ssh)
    monkeypatch.setattr(spool, "_rsync", rsync)
    srv = HTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield home, f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def worker(home, url):
    return subprocess.run([sys.executable, str(home / "litnav-spool" / "run_spool.py"), str(home / "litnav-spool"),
                           "--url", url, "--model", "m", "-j", "4"], capture_output=True, text=True)


def configure(lib):
    lib.config["batch_remote"] = {"host": "cluster", "spool": "~/litnav-spool"}


def test_push_work_pull_verify(ilib, remote):
    home, url = remote
    lib = strict(ilib)
    configure(lib)
    localmodel.enqueue(lib, [localmodel.verify_job(lib, "smith2021cold", "cold pools spread"),
                             localmodel.verify_job(lib, "smith2021cold", "cold pools shrink")])
    r = spool.push(lib, log=lambda m: None)
    assert r["jobs"] == 2 and not r["worker"]         # no submit command configured
    assert spool.push(lib, log=lambda m: None)["jobs"] == 0  # already in a batch
    sp = home / "litnav-spool"
    assert (sp / "inbox" / r["batch"] / "jobs.jsonl").is_file()
    assert oct((sp).stat().st_mode)[-3:] == "700"
    assert spool.pull(lib, log=lambda m: None)[r["batch"]].startswith("waiting")
    w = worker(home, url)
    assert w.returncode == 0, w.stderr + w.stdout
    assert json.loads((sp / "status.json").read_text())["state"] == "idle"
    s = spool.pull(lib, log=lambda m: None)
    assert s[r["batch"]].startswith("done: 2/2")
    assert not (sp / "done" / r["batch"]).exists() and not spool.local_dir(lib, r["batch"]).exists()
    log = [json.loads(l) for l in localmodel.verify_log(lib).read_text().splitlines()]
    assert sorted(x["verdict"] for x in log) == ["contradicted", "supported"]
    assert all(x["via"] == "batch" and x["location"]["passages"] for x in log)
    assert localmodel.queued(lib) == []
    assert spool.pull(lib, log=lambda m: None) == {}


def test_interrupted_batch_resumes_and_eval_imports(ilib, remote, tmp_path):
    home, url = remote
    lib = strict(ilib)
    configure(lib)
    ev = tmp_path / "ev.jsonl"
    ev.write_text(json.dumps({"id": "c01", "key": "smith2021cold", "type": "true", "claim": "Cold pools spread.",
                              "ideal": "supported", "acceptable": ["supported"]}) + "\n")
    r = modeleval.push(lib, ev, log=lambda m: None)
    sp = home / "litnav-spool"
    b = sp / "inbox" / r["batch"]
    first = json.loads((b / "jobs.jsonl").read_text().splitlines()[0])
    b.rename(sp / "running" / r["batch"])                    # as if a killed job left it half done
    (sp / "running" / r["batch"] / "results.jsonl").write_text(
        json.dumps({"id": first["id"], "ok": True, "output": {"verdict": "supported"}, "model": "big-model",
                    "raw": "{}", "seconds": 1}) + "\n" + '{"id": "cut off')
    Model.calls = 0
    assert worker(home, url).returncode == 0
    assert Model.calls == 1                                   # only the job not done before
    spool.pull(lib, log=lambda m: None)
    rd = spool.load_state(lib)[r["batch"]]["run_dir"]
    from pathlib import Path
    rep = modeleval.report(lib, ev, Path(rd))
    assert rep["summary"]["by_variant"]["A"]["n"] == 1 and rep["summary"]["by_variant"]["B"]["ok"] == 1


def test_a_model_profile_has_its_own_spool(ilib, monkeypatch):
    """-M gpt-oss pushes to gpt-oss's spool, so the 235B worker never takes its batches."""
    from litnav import spool
    configure(ilib)
    ilib.config["batch_remote"]["profiles"] = {"gpt-oss": {"spool": "~/spool-gpt-oss", "job_name": "llm-gpt-oss"}}
    monkeypatch.delenv("LITNAV_MODEL_PROFILE", raising=False)
    assert spool.remote_cfg(ilib)["spool"] == "~/litnav-spool" and "profiles" not in spool.remote_cfg(ilib)
    monkeypatch.setenv("LITNAV_MODEL_PROFILE", "gpt-oss")
    c = spool.remote_cfg(ilib)
    assert c["spool"] == "~/spool-gpt-oss" and c["host"] == "cluster" and c["job_name"] == "llm-gpt-oss"
    monkeypatch.setenv("LITNAV_MODEL_PROFILE", "orchid")              # a profile with no spool of its own
    assert spool.remote_cfg(ilib)["spool"] == "~/litnav-spool"


def test_pull_and_status_see_only_their_own_spool(ilib, monkeypatch):
    """Two models' batches in one state file: each spool's pull leaves the other's alone."""
    from litnav import spool
    configure(ilib)
    ilib.config["batch_remote"]["profiles"] = {"gpt-oss": {"spool": "~/spool-gpt-oss"}}
    spool.save_state(ilib, {"a": {"state": "pushed", "kind": "queue", "jobs": [], "imported": []},   # old: default
                            "b": {"state": "pushed", "kind": "queue", "jobs": [], "imported": [],
                                  "spool": "~/spool-gpt-oss"}})
    seen = []
    monkeypatch.setattr(spool, "_remote_where", lambda cfg, ids: seen.append(sorted(ids)) or {})
    monkeypatch.delenv("LITNAV_MODEL_PROFILE", raising=False)
    spool.pull(ilib, log=lambda *a: None)
    monkeypatch.setenv("LITNAV_MODEL_PROFILE", "gpt-oss")
    spool.pull(ilib, log=lambda *a: None)
    assert seen == [["a"], ["b"]]


def test_rerun_sets_a_papers_results_aside(tmp_path):
    from litnav import papereval as P
    P.save(tmp_path / "jobs.jsonl", [{"id": "m-a--runs", "key": "a"}, {"id": "m-a--g01", "key": "a"},
                                     {"id": "m-b--g01", "key": "b"}])
    P.save(tmp_path / "results.jsonl", [{"id": "m-a--g01", "ok": True}, {"id": "m-a--g01-r", "ok": True},
                                        {"id": "m-b--g01", "ok": True}])
    jobs = P.rerun_jobs(tmp_path, ["a"])
    assert [j["id"] for j in jobs] == ["m-a--runs", "m-a--g01"]
    assert [r["id"] for r in P.load(tmp_path / "results.jsonl")] == ["m-b--g01"]
    (aside,) = tmp_path.glob("results.rerun-*.jsonl")
    assert len(P.load(aside)) == 2
