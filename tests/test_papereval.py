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
