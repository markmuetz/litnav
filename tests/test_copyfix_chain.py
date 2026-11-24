"""The runner chains a mentions job's rewrite and meaning check in the same run."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from litnav import batch_runner, copyfix

PASSAGE = ("Cold pools spread out beneath precipitating clouds and lift the surrounding moist air "
           "until new convective cells form along the gust front within about 30 minutes.")
COPY = "Cold pools spread out beneath precipitating clouds and lift the surrounding moist air until new cells form."


def job():
    return {"id": "mentions-k--g01", "key": "k", "followup": "mentions", "title": "Cold pools",
            "schema": {"type": "object"},
            "messages": [{"role": "system", "content": "Extract."},
                         {"role": "user", "content": f"Passages:\n[k#1] {PASSAGE}"}]}


def fake_chat(calls):
    def chat(url, model, messages, schema=None, **kw):
        calls.append(messages)
        if messages[0]["content"] == copyfix.MEANING_SYSTEM:
            return json.dumps({"checks": [{"n": 2, "verdict": "same", "reason": "ok"}]}), "m"
        if len(messages) > 2:
            return json.dumps({"rewrites": [{"n": 2, "statement":
                                             "Gust fronts of cold pools set off new cells in about 30 minutes."}]}), "m"
        return json.dumps({"mentions": [{"statement": "Cold pools matter."},
                                        {"statement": COPY + " within about 30 minutes"}]}), "m"
    return chat


def test_chain_runs_in_the_same_run(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(batch_runner, "chat", fake_chat(calls))
    jobs, res = tmp_path / "jobs.jsonl", tmp_path / "results.jsonl"
    jobs.write_text(json.dumps(job()) + "\n")
    n = batch_runner.run(jobs, res, "http://x", "m", concurrency=4, log=lambda *a: None)
    rows = {r["id"]: r for r in map(json.loads, res.read_text().splitlines())}
    assert n == 3 and set(rows) == {"mentions-k--g01", "mentions-k--g01-r", "mentions-k--g01-c"}
    assert rows["mentions-k--g01-r"]["rewrite"] == [2] and rows["mentions-k--g01-c"]["meaning_check"] == [2]
    assert batch_runner.todo_jobs(jobs, res) == []                 # nothing left: the batch is done
    assert batch_runner.run(jobs, res, "http://x", "m", log=lambda *a: None) == 0


def test_a_stopped_chain_is_picked_up(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(batch_runner, "chat", fake_chat(calls))
    jobs, res = tmp_path / "jobs.jsonl", tmp_path / "results.jsonl"
    jobs.write_text(json.dumps(job()) + "\n")
    first = batch_runner.run_job(job(), "http://x", "m")            # the main job only, then "killed"
    res.write_text(json.dumps(first) + "\n")
    assert [j["id"] for j in batch_runner.todo_jobs(jobs, res)] == ["mentions-k--g01-r"]
    batch_runner.run(jobs, res, "http://x", "m", log=lambda *a: None)
    assert {json.loads(l)["id"] for l in res.read_text().splitlines()} >= {"mentions-k--g01-r",
                                                                          "mentions-k--g01-c"}


def test_no_copy_no_followup(tmp_path, monkeypatch):
    monkeypatch.setattr(batch_runner, "chat",
                        lambda url, model, messages, **kw: (json.dumps({"mentions": [{"statement": "Fine."}]}), "m"))
    jobs, res = tmp_path / "jobs.jsonl", tmp_path / "results.jsonl"
    jobs.write_text(json.dumps(job()) + "\n")
    assert batch_runner.run(jobs, res, "http://x", "m", log=lambda *a: None) == 1


def test_shipped_files_run_with_the_standard_library_alone(tmp_path):
    src = Path(batch_runner.__file__).parent
    shutil.copyfile(src / "batch_runner.py", tmp_path / "run_jobs.py")
    shutil.copyfile(src / "copyfix.py", tmp_path / "copyfix.py")
    code = ("import run_jobs, copyfix; assert run_jobs.copyfix is not None; "
            "print(copyfix.statements_copied(['a b c d e f g h i j k l m n'], "
            "copyfix.ngrams('a b c d e f g h i j k l m n o p')))")
    p = subprocess.run([sys.executable, "-S", "-c", code], cwd=tmp_path, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert '"n": 1' in p.stdout.replace("'", '"')


def test_pull_imports_the_followups(tmp_path):
    from litnav import spool
    d, rd = tmp_path / "batch", tmp_path / "run"
    d.mkdir()
    rd.mkdir()
    (rd / "run.json").write_text("{}")
    (d / "jobs.jsonl").write_text(json.dumps(job()) + "\n")
    rows = [{"id": "mentions-k--g01", "ok": True, "output": {"mentions": []}, "model": "m"},
            {"id": "mentions-k--g01-r", "ok": True, "output": {"rewrites": []}, "rewrite": [2], "model": "m"},
            {"id": "mentions-k--g01-c", "ok": True, "output": {"checks": []}, "meaning_check": [2], "model": "m"},
            {"id": "something-else", "ok": True, "output": {}, "model": "m"}]
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    b = {"kind": "paper-eval", "run_dir": str(rd), "task": "mentions", "jobs": ["mentions-k--g01"], "imported": []}
    assert spool._apply(None, "b1", b, d, log=lambda *a: None) == 3
    got = [json.loads(l)["id"] for l in (rd / "results.jsonl").read_text().splitlines()]
    assert got == ["mentions-k--g01", "mentions-k--g01-r", "mentions-k--g01-c"]
