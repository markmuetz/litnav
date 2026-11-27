"""G1 run with the G1 prompt of a G0 draft."""
import json

import pytest
from test_localmodel import strict
from test_search import ilib  # noqa: F401

from litnav import claims, copyfix, g1run

PROMPT = "You are extracting a claim graph. " * 30


def draft(tmp_path, prompt=PROMPT):
    p = tmp_path / "draft-2.json"
    p.write_text(json.dumps({"model": "claude-opus-5-5", "schema": {"g1_prompt": prompt}}))
    return p


def test_jobs_index_first_then_sections_waiting_for_it(ilib, tmp_path):
    lib = strict(ilib)
    rd = g1run.new_run(lib, draft(tmp_path))
    js = [json.loads(l) for l in (rd / "jobs.jsonl").read_text().splitlines()]
    keys = {j["key"] for j in js}
    assert "doe2026plans_draft" not in keys and keys                  # never the confidential draft
    for k in keys:
        mine = [j for j in js if j["key"] == k]
        assert mine[0]["role"] == "runs" and mine[0]["id"].endswith("--runs")
        assert all(j["after"] == mine[0]["id"] and claims.RUNS_SLOT in j["messages"][1]["content"]
                   and j["messages"][0]["content"].startswith(PROMPT) for j in mine[1:])
    m = json.loads((rd / "run.json").read_text())
    assert m["prompt_from"]["drafted_by"] == "claude-opus-5-5" and (rd / "prompt.md").read_text() == PROMPT
    with pytest.raises(ValueError):
        g1run.draft_prompt(draft(tmp_path, prompt="short"))


def test_an_induced_index_fills_the_slot_and_records_take_rewrites(ilib, tmp_path):
    lib = strict(ilib)
    rd = g1run.new_run(lib, draft(tmp_path), keys=["jones2019gravity"])
    js = [json.loads(l) for l in (rd / "jobs.jsonl").read_text().splitlines()]
    ix, sec = js[0], js[1]
    entry = {"name": "CTL", "evidence_kind": "realistic_simulation", "model_or_method": {"name": "UM"}, "grid_spacing": "4 km"}
    res = {ix["id"]: {"id": ix["id"], "ok": True, "output": {"experiments": [entry]}}}
    filled = copyfix.fill(sec, res)
    assert '"CTL"' in filled["messages"][1]["content"] and claims.RUNS_SLOT not in filled["messages"][1]["content"]
    rows = [res[ix["id"]],
            {"id": sec["id"], "ok": True, "output": {"experiments": [{"name": "EXP"}], "mentions": [
                {"statement": "copied words here", "chunk": sec["passages"][0]},
                {"statement": "fine", "chunk": sec["passages"][0]}]}},
            {"id": sec["id"] + "-r", "ok": True, "rewrite": [1],
             "output": {"rewrites": [{"n": 1, "statement": "in own words"}]}},
            {"id": sec["id"] + "-c", "ok": True, "meaning_check": [1],
             "output": {"checks": [{"n": 1, "verdict": "same"}]}}]
    (rd / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    rec = g1run.records(rd)["jones2019gravity"]
    assert [e["name"] for e in rec["experiments"]] == ["CTL", "EXP"]
    assert [m["statement"] for m in rec["mentions"]] == ["in own words", "fine"]
    assert len(rec["failed"]) == len(js) - 2                          # the other groups have no result
    rep = g1run.report(rd)
    assert rep["papers"] == 1 and rep["mentions"] == 2 and rep["fields"]["chunk"] == 2


def test_open_schemas_are_not_sent_as_structured_outputs():
    from litnav import batch_runner
    assert not batch_runner.closed(g1run.section_schema(["a"]))
    assert batch_runner.closed(claims.schema(["a"]))


def test_an_induced_mention_reads_as_a_v7_mention():
    m = g1run.as_mention({"statement": "s", "chunk": "c", "claim_type": "shown", "hedging": "firm",
                          "citations": ["Smith 2001"], "rests_on": ["CTL"], "_job": "x",
                          "conditions": [{"dimension": "region", "value": "Sahel"}]})
    assert m["hedging"] == "strong" and m["cites"] == "Smith 2001" and m["experiment_name"] == "CTL"
    assert m["conditions"] == {"region": "Sahel"} and "_job" not in m


def test_a_batch_with_its_own_results_file_keeps_them_apart(tmp_path):
    (tmp_path / "run.json").write_text("{}")
    g1run.import_batch(None, {"run_dir": str(tmp_path), "results": "q-results.jsonl"}, {},
                       [{"id": "x", "ok": True}], {"model": "m"}, log=lambda *a: None)
    assert (tmp_path / "q-results.jsonl").is_file() and not (tmp_path / "results.jsonl").exists()
    assert json.loads((tmp_path / "run.json").read_text()) == {}
