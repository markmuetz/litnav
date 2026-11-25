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


RUN = {"kind": "model_run", "name": "CTRL", "region": "", "method": "", "method_cited": "", "datasets": [],
       "model": "UM", "version": "", "configuration": "RA2T", "configuration_cited": "",
       "family": "physical", "horizontal_grid_spacing": "2.2 km", "vertical_levels": "", "period": "",
       "domain": "limited_area", "coupling": "atmosphere_only", "ocean_model": "",
       "components": [{"kind": "deep_convection", "state": "off", "scheme": "", "moments": "", "chunk": "k#1"},
                      {"kind": "microphysics", "state": "used", "scheme": "CASIM", "moments": "2", "chunk": "k#1"}]}


OBS = {"kind": "observational_analysis", "name": "radar cold pools", "region": "Oklahoma", "period": "2011",
       "method": "tracking with TOBAC", "method_cited": "", "components": [], "family": "unknown",
       "datasets": [{"name": "NEXRAD", "version": "Level II", "kind": "radar", "resolution": "", "chunk": "k#1"}]}


def test_section_calls_wait_for_the_runs_table(tmp_path, monkeypatch):
    """v4: the runs call goes first on the node; section calls start after it, with its table in their prompt."""
    from litnav import claims as C
    order = []

    def chat(url, model, messages, schema=None, **kw):
        order.append(messages[0]["content"][:12])
        if messages[0]["content"] == C.RUNS_SYSTEM:
            return json.dumps({"experiments": [RUN, OBS]}), "m"
        assert C.RUNS_SLOT not in messages[1]["content"]
        assert "model run 'CTRL': UM, RA2T, 2.2 km, limited_area; deep convection: off; microphysics: CASIM" \
            in messages[1]["content"]
        assert "observational analysis 'radar cold pools': NEXRAD Level II; tracking with TOBAC" in messages[1]["content"]
        return json.dumps({"mentions": [{"statement": "Cold pools matter.", "experiment_name": "CTRL",
                                         "evidence": "model"}]}), "m"
    monkeypatch.setattr(batch_runner, "chat", chat)
    runs = {"id": "mentions-k--runs", "key": "k", "role": "runs", "schema": {"type": "object"},
            "messages": [{"role": "system", "content": C.RUNS_SYSTEM}, {"role": "user", "content": "Passages:\n"}]}
    secs = [{**job(), "id": f"mentions-k--g0{n}", "after": runs["id"],
             "messages": [{"role": "system", "content": "Extract."},
                          {"role": "user", "content": f"Runs:\n{C.RUNS_SLOT}\n\nPassages:\n[k#1] {PASSAGE}"}]}
            for n in (1, 2)]
    jobs, res = tmp_path / "jobs.jsonl", tmp_path / "results.jsonl"
    jobs.write_text("".join(json.dumps(j) + "\n" for j in secs + [runs]))   # the runs call listed last
    n = batch_runner.run(jobs, res, "http://x", "m", concurrency=8, log=lambda *a: None)
    assert n == 3 and order[0] == C.RUNS_SYSTEM[:12]
    assert batch_runner.todo_jobs(jobs, res) == []


def test_evidence_type_from_the_runs_table():
    from litnav import claims as C
    ml = {**RUN, "name": "GC", "family": "machine_learning", "components": []}
    param = {**RUN, "name": "GCM", "horizontal_grid_spacing": "100 km",
             "components": [{"kind": "deep_convection", "state": "used", "scheme": "Gregory-Rowntree",
                             "moments": "", "chunk": ""}]}
    les = {**RUN, "name": "LES", "horizontal_grid_spacing": "100 m"}
    unknown = {**RUN, "name": "KM", "components": []}                        # scheme not stated
    old = {k: v for k, v in RUN.items() if k not in ("kind", "name")} | {"run_name": "OLD"}   # first-draft entry
    runs = [RUN, ml, param, les, unknown, OBS, old]
    m = {"evidence": "model", "experiment": {}}
    ex = lambda name, **kw: {**m, "experiment_name": name, **kw}
    assert C.evidence_type(ex("ctrl"), runs) == "cpm"                        # names match without case
    assert C.evidence_type(ex("GC"), runs) == "unknown"
    assert C.evidence_type(ex("GCM"), runs) == "parametrized_model"
    assert C.evidence_type(ex("LES"), runs) == "les"
    assert C.evidence_type(ex("KM"), runs) == "unknown"
    assert C.evidence_type(ex("OLD"), runs) == "cpm"
    assert C.evidence_type(ex("other"), runs) == "unknown"                   # falls back to its own facts
    assert C.evidence_type(ex("radar cold pools"), runs) == "unknown"        # not a model run
    assert C.evidence_type(ex("radar cold pools", evidence="observations"), runs) == "observations"
    recs, _ = C.records("k", [{"mentions": [{**ex("ctrl"), "statement": "s"}]}], {}, runs=runs)
    assert recs[0]["experiment_name"] == "CTRL" and recs[0]["evidence_type"] == "cpm"


def test_methods_rows_pick_the_methods():
    from litnav import claims as C
    rows = [{"id": "a", "kind": "abstract", "section_title": "Abstract", "text": "x"},
            {"id": "i", "kind": "body", "section_title": "1 Introduction", "text": "i" * 10},
            {"id": "m", "kind": "body", "section_title": "2 Model and experiments", "text": "m" * 10},
            {"id": "r", "kind": "body", "section_title": "3 Results", "text": "r" * 10}]
    assert [r["id"] for r in C.methods_rows(rows)] == ["m"]
    assert [r["id"] for r in C.methods_rows([rows[1], rows[3]])] == ["i", "r"]   # no methods title: the body


def test_evidence_rule_2():
    """Mark, 25 Nov 2026: not stated and coarser than 10 km -> parametrized (inferred); idealized models."""
    from litnav import claims as C
    m = lambda dx, param="unknown": {"evidence": "model", "experiment": {"resolution": dx,
                                                                         "deep_convection_parametrized": param}}
    assert C.evidence_type(m("1 degree")) == "parametrized_model" and C.evidence_type(m("1 degree"), rule=1) == "unknown"
    assert C.evidence_inferred(m("25 km"))
    assert C.evidence_type(m("10 km")) == "unknown"                      # 10 km itself: not beyond 10 km
    assert C.evidence_type(m("5 and 10 km")) == "unknown"
    assert C.evidence_type(m("25 km", "no")) == "cpm" and not C.evidence_inferred(m("25 km", "no"))   # said explicit
    assert C.evidence_type(m("4 km and 50 km")) == "mixed"               # unknown at 4 km, inferred at 50 km
    assert C.evidence_type(m("")) == "unknown"
    qg = {**RUN, "name": "QG", "family": "idealized", "components": []}
    x = {"evidence": "model", "experiment_name": "QG", "experiment": {}}
    assert C.evidence_type(x, [qg]) == "idealized_model" and C.evidence_type(x, [qg], rule=1) == "unknown"
    gcm = {**RUN, "name": "CAM", "horizontal_grid_spacing": "0.9 degrees", "components": []}
    assert C.evidence_type({**x, "experiment_name": "CAM"}, [gcm]) == "parametrized_model"


def test_degree_sign_look_alikes():
    from litnav import claims as C
    assert C.grid_spacings_m("~ 0.9◦ latitude and 1.25◦ longitude") == [0.9 * 111_000, 1.25 * 111_000]
    assert C.grid_spacing_m("1º") == 111_000 and C.grid_spacing_m("T63") is None    # spectral: converted later


def test_cites_kept_and_shown_to_the_judge():
    from types import SimpleNamespace
    from litnav import claims as C, papereval as P
    m = {"statement": "Cold pools trigger new cells.", "claim_type": "restated", "cites": "Davis (2015)",
         "evidence": "model", "chunk": "k#1"}
    recs, _ = C.records("k", [{"mentions": [m]}], {})
    assert recs[0]["cites"] == "Davis (2015)"
    assert "cites" in C.schema(["k#1"])["properties"]["mentions"]["items"]["required"]
    it = SimpleNamespace(title="T", year=2020)
    prompt = P.judge_prompt("mentions", it, "text", [{"id": "k1", "point": "p"}], {"mentions": [m]})
    assert "cites 'Davis (2015)'" in prompt and "cites_ok, where the claim shows cites" in prompt
    old = P.judge_prompt("mentions", it, "text", [], {"mentions": [{k: v for k, v in m.items() if k != "cites"}]})
    assert "cites_ok: not_given" in old


def test_lean_experiments_table_v7():
    """Framework v2: the general pass's table is an index; deep convection is one field."""
    from litnav import claims as C, copyfix
    props = C.runs_schema(["k#1"])["properties"]["experiments"]["items"]["properties"]
    assert "components" not in props and "deep_convection" in props and "coupling" not in props
    lean = {"kind": "model_run", "name": "CTRL", "model": "UM", "configuration": "RA2T", "family": "physical",
            "horizontal_grid_spacing": "2.2 km", "deep_convection": "off", "deep_convection_chunk": "k#1",
            "region": "", "period": "", "datasets": [], "method": ""}
    gcm = {**lean, "name": "GCM", "horizontal_grid_spacing": "N96", "deep_convection": "used"}
    m = {"evidence": "model", "experiment_name": "CTRL", "experiment": {}}
    assert C.evidence_type(m, [lean, gcm]) == "cpm"
    assert C.evidence_type({**m, "experiment_name": "GCM"}, [lean, gcm]) == "parametrized_model"
    assert "model run 'CTRL': UM, RA2T, 2.2 km; deep convection: off" in copyfix.runs_table([lean])
