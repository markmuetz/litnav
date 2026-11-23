"""paper-eval: pick, gold, run, judge, report, registry; the existing summaries as a baseline.
The local model and Claude are stand-ins."""
import json

import pytest

from litnav import derived, papereval as P
from litnav.net import Refused

from test_localmodel import FakeModel, ilib, strict  # noqa: F401


def fake_claude(monkeypatch, seen):
    def run_claude(cmd, prompt, timeout=600):
        seen.append(prompt)
        schema = json.loads(cmd[cmd.index("--json-schema") + 1])
        if "key_points" in schema["properties"] and schema["properties"]["key_points"]["type"] == "array":
            out = {"key_points": [{"id": "k1", "point": "Gravity waves radiate", "kind": "finding", "core": True},
                                  {"id": "k2", "point": "Squall lines", "kind": "data", "core": False}]}
        elif "statements" in schema["properties"]:
            out = {"statements": [{"statement": "a", "verdict": "supported", "note": ""},
                                  {"statement": "b", "verdict": "wrong", "note": "sign"}],
                   "key_points_covered": ["k1"], "keywords_ok": True}
        elif "claim_type" in schema["properties"]["claims"]["items"]["properties"]:
            out = {"claims": [{"n": 1, "verdict": "correct", "claim_type": "restated", "evidence_type": "theory",
                               "hedging": "strong", "standalone": True, "note": ""},
                              {"n": 2, "verdict": "wrong_detail", "claim_type": "shown",
                               "evidence_type": "observations", "hedging": "moderate", "standalone": False,
                               "note": ""}],
                   "key_points_covered": {"k1": [2], "k2": []}}
        else:
            out = {"claims": [{"n": 1, "verdict": "correct", "strength_ok": True, "standalone": True, "note": ""}],
                   "key_points_covered": {"k1": [1], "k2": []}}
        return {"structured_output": out, "modelUsage": {"claude-opus-5-5": {}}}
    monkeypatch.setattr(derived, "run_claude", run_claude)


def test_paper_eval_end_to_end(ilib, tmp_path, monkeypatch):
    lib = strict(ilib)
    rows = P.pick(lib, 5, min_chars=0)
    assert [r["key"] for r in rows] == ["jones2019gravity"]          # open only: not licensed or confidential
    ev = lib.root / "eval" / "paper-eval.jsonl"
    ev.parent.mkdir(exist_ok=True)
    P.save(ev, rows)
    seen = []
    fake_claude(monkeypatch, seen)
    assert P.gold(lib, ev, log=lambda m: None) == 1
    g = P.load(ev)[0]
    assert g["key_points"][0]["core"] and g["gold"]["model"] == "claude-opus-5-5"

    def reply(messages):
        if "extract the claims" in messages[0]["content"]:
            ids = [l[1:].split("]")[0] for l in messages[1]["content"].splitlines() if l.startswith("[")]
            return {"claims": [{"claim": "Gravity waves radiate from squall lines observed by radar.",
                                "kind": "finding", "numbers": [], "conditions": "", "strength": "shown",
                                "passages": ids[:1]}]}
        return {"topic": "t", "summary": "Gravity waves radiate. They are seen.", "keywords": ["waves"]}
    FakeModel(monkeypatch, reply)
    monkeypatch.setattr("litnav.localmodel.model_info", lambda lib: {"model": "fake-14b", "machine": "m"})
    for task in ("summary", "extract"):
        rd = P.run(lib, ev, task, tmp_path / task, log=lambda m: None)
        assert P.judge(lib, ev, rd, log=lambda m: None) == 1
        assert P.judge(lib, ev, rd, log=lambda m: None) == 0             # judged once
        rep = P.report(lib, ev, rd)
        P.record(lib, rd, rep["summary"], registry=tmp_path / "runs.jsonl")
        s = rep["summary"]
        assert s["core_coverage"] == 1.0
        if task == "summary":
            assert s["precision"] == 0.5 and s["verdicts"] == {"supported": 1, "wrong": 1}
        else:
            assert s["claims"] == 1 and s["passages_valid"] == 1.0 and s["kp_coverage"] == 0.5
    reg = P.load(tmp_path / "runs.jsonl")
    assert [r["task"] for r in reg] == ["summary", "extract"]
    assert reg[0]["model"] == "fake-14b" and reg[0]["model_info"]["machine"] == "m"
    assert reg[0]["judge"]["model"] == "claude-opus-5-5" and reg[0]["eval_sha256"]


def test_judge_and_gold_refuse_licensed_papers(ilib, monkeypatch):
    lib = strict(ilib)
    fake_claude(monkeypatch, [])
    ev = lib.root / "ev.jsonl"
    P.save(ev, [{"key": "smith2021cold"}])
    with pytest.raises(Refused):
        P.gold(lib, ev, log=lambda m: None)


def test_copied_run():
    grams = P._ngrams("one two three four five six seven eight nine ten")
    assert P.copied_run("x one two three four five six seven y", grams) == 7
    assert P.copied_run("one two three four five", grams) == 0


def test_chars_is_a_run_setting(ilib, tmp_path, monkeypatch):
    lib = strict(ilib)
    ev = lib.root / "eval" / "pe.jsonl"
    ev.parent.mkdir(exist_ok=True)
    P.save(ev, [{"key": "jones2019gravity", "key_points": [{"id": "k1", "point": "p", "kind": "finding",
                                                            "core": True}]}])
    FakeModel(monkeypatch, lambda m: {"claims": []})
    monkeypatch.setattr("litnav.localmodel.model_info", lambda lib: {"model": "fake"})
    whole = len(lib.get("jones2019gravity").text())
    rd = P.run(lib, ev, "extract", tmp_path / "whole", chars=0, log=lambda m: None)
    job = P.load(rd / "jobs.jsonl")[0]
    assert json.loads((rd / "run.json").read_text())["eval_chars"] == 0
    assert job["chars_sent"] >= whole * 0.9                         # the whole paper, as passages
    rd2 = P.run(lib, ev, "extract", tmp_path / "short", chars=200, log=lambda m: None)
    assert P.load(rd2 / "jobs.jsonl")[0]["chars_sent"] <= 200
    P.run(lib, ev, "extract", rd2, log=lambda m: None)              # resuming keeps it
    assert json.loads((rd2 / "run.json").read_text())["eval_chars"] == 200
    with pytest.raises(ValueError):
        P.run(lib, ev, "extract", rd2, chars=0, log=lambda m: None)
    rep = P.report(lib, ev, rd2)
    assert rep["rows"][0]["paper_chars"] == whole
    assert set(rep["summary"]["by_length"]) == {"<=72k", ">72k"}
    assert rep["summary"]["by_length"]["<=72k"]["papers"] == 1


def test_compare_runs_item_by_item():
    def row(key, hits, good, bad):
        return {"key": key, "judged": True, "core_hits": hits, "kp_hits": hits, "_core_ids": ["k1", "k2"],
                "_all_ids": ["k1", "k2"], "verdicts": {"correct": good, "wrong": bad}}
    a = [row("p1", ["k1", "k2"], 4, 0), row("p2", ["k1"], 3, 1)]
    b = [row("p1", ["k1"], 2, 2), row("p2", [], 2, 2)]
    c = P.compare("extract", a, b)
    k = c["key_points_core"]
    assert (k["n"], k["a"], k["b"], k["a_only"], k["b_only"]) == (4, 3, 1, 2, 0)
    assert k["a_only_ids"] == ["p1:k2", "p2:k1"]
    assert c["precision_diff"]["diff"] == round(7 / 8 - 4 / 8, 3)


def test_record_keeps_hand_added_model_info(tmp_path, ilib):
    rd = tmp_path / "run"
    rd.mkdir()
    (rd / "run.json").write_text(json.dumps({"task": "verify", "created": "2026-11-22"}))
    reg = tmp_path / "runs.jsonl"
    P.save(reg, [{"run": str(rd), "model": "m", "model_info": {"gpus": "8"}, "scores": {}}])
    P.record(ilib, rd, {"x": 1}, registry=reg)
    (r,) = P.load(reg)
    assert r["model_info"] == {"gpus": "8"} and r["model"] == "m" and r["scores"] == {"x": 1}


def test_mentions_task(ilib, tmp_path, monkeypatch):
    lib = strict(ilib)
    ev = lib.root / "eval" / "pe.jsonl"
    ev.parent.mkdir(exist_ok=True)
    P.save(ev, [{"key": "jones2019gravity", "key_points": [
        {"id": "k1", "point": "p", "kind": "finding", "core": True},
        {"id": "k2", "point": "q", "kind": "data", "core": False}]}])
    calls = []

    def reply(messages):
        calls.append(messages)
        lines = messages[1]["content"].split("Passages:\n", 1)[1].splitlines()
        ids = [l[1:].split("]")[0] for l in lines if l.startswith("[")]
        text = lines[1]
        m = {"statement": "Gravity waves radiate from squall lines.", "chunk": ids[0], "quote": text[:30],
             "source": "text", "claim_type": "shown", "evidence_type": "theory",
             "experiment": {"model": "", "resolution": "", "method": ""},
             "conditions": {"region": "", "regime": "", "period": "", "resolution": ""},
             "hedging": "strong", "hedge_words": "", "value": {"value": "", "units": "", "low": "", "high": ""}}
        return {"mentions": [m, {**m, "quote": "words that are not there", "claim_type": "shown"}]}
    FakeModel(monkeypatch, reply)
    monkeypatch.setattr("litnav.localmodel.model_info", lambda lib: {"model": "fake"})
    fake_claude(monkeypatch, [])
    rd = P.run(lib, ev, "mentions", tmp_path / "m", chars=300, log=lambda m: None)
    jobs = P.load(rd / "jobs.jsonl")
    assert len(jobs) == len(calls) >= 2                     # the paper in several calls, by section
    assert all("Abstract:" in c[1]["content"] or "Opening (no abstract" in c[1]["content"] for c in calls)
    assert json.loads((rd / "run.json").read_text())["eval_chars"] == 300
    assert P.judge(lib, ev, rd, log=lambda m: None) == 1      # one judgment for the paper
    s = P.report(lib, ev, rd)["summary"]
    assert s["claims"] == 2 * len(jobs) and s["claims_per_paper"] == 2 * len(jobs)
    assert s["grounded"] == 0.5
    assert s["claim_type_accuracy"] == 0.5 and s["restated_as_shown"] == 1 and s["restated_total"] == 1
    assert s["claim_type_confusion"] == {"restated->shown": 1}
    assert s["hedging_accuracy"] == 0.5 and s["core_coverage"] == 1.0


def test_mention_groups_and_records():
    from litnav import claims as C
    rows = [{"id": "a1", "section": "1", "kind": "abstract", "text": "x" * 50},
            {"id": "f", "section": "0", "kind": "front", "text": "title"},
            {"id": "b1", "section": "2", "kind": "body", "text": "y" * 60},
            {"id": "b2", "section": "2", "kind": "body", "text": "y" * 60},
            {"id": "c1", "section": "3", "kind": "body", "text": "z" * 30},
            {"id": "k", "section": "4", "kind": "ack", "text": "thanks"}]
    g = C.groups(rows, budget=100)
    assert [[r["id"] for r in x] for x in g] == [["a1"], ["b1"], ["b2", "c1"]]
    assert [[r["id"] for r in x] for x in C.groups(rows, budget=10_000)] == [["a1", "b1", "b2", "c1"]]
    m = {"statement": "s", "chunk": "b1", "quote": "q", "source": "figure", "claim_type": "restated",
         "evidence_type": "km_scale", "experiment": {}, "conditions": {"region": "Sahel"}, "hedging": "weak",
         "hedge_words": "may", "value": {"value": "1,200", "units": "m", "low": "", "high": "1500"}}
    recs, quotes = C.records("k", [{"mentions": [m]}, None], {"b1": {"section_title": "Results", "page": 3}})
    (r,) = recs
    assert r["id"].startswith("m_") and "quote" not in r and quotes[0]["mention"] == r["id"]
    assert r["value"] == {"value": 1200.0, "as_written": "1,200", "units": "m", "low": None, "high": 1500.0}
    assert r["hedging"] == {"level": "weak", "words": "may"} and r["section"] == "Results" and r["page"] == 3
    assert C._value({"value": "", "units": "", "low": "", "high": ""}) is None
    assert C.grounded("radiate from squall-\nlines", "Waves RADIATE   from squall-\n lines here")
    assert not C.grounded("", "anything")
