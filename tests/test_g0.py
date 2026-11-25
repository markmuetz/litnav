"""G0: blind induction of the domain schema."""
import json
import random
import re

import pytest
from test_localmodel import strict
from test_search import ilib  # noqa: F401

from litnav import derived, g0


def test_prompts_are_domain_free():
    """The run is blind: no word from the hand-built schema of this library's field."""
    text = " ".join([g0.SURVEY_SYSTEM, g0.INVENTORY_SYSTEM, g0.SYNTH_SYSTEM, g0.REVIEW_SYSTEM, g0.CORE,
                     g0.PURPOSE]).lower()
    for w in ["convect", "cloud", "precipit", "atmospher", "weather", "climate", "grid spacing", "reanalysis",
              "parametri", r"\bmcss?\b", r"\bles\b", "storm", "resolution"]:
        assert not re.search(w, text), w


def test_stratified_takes_one_from_every_stratum_and_n_in_all():
    strata = {"a": [f"a{i}" for i in range(80)], "b": [f"b{i}" for i in range(15)], "c": ["c0"]}
    got = g0.stratified(strata, 20, random.Random(1))
    assert len(got) == 20 and "c0" in got and sum(k.startswith("b") for k in got) >= 2


def test_sample_push_tally_and_synthesis(ilib, monkeypatch):
    lib = strict(ilib)
    rd = g0.sample(lib, n=5)
    keys = {json.loads(l)["key"] for l in (rd / "sample.jsonl").read_text().splitlines()}
    assert keys == {"smith2021cold", "jones2019gravity"}            # never the confidential draft
    js = g0.jobs(lib, rd)
    assert {j["step"] for j in js} == {"survey", "inventory"}
    assert all("doe2026plans_draft" not in j["id"] for j in js)

    smith = lib.get("smith2021cold")
    copied = " ".join(smith.text().split()[20:36])                   # 16 words straight from the paper
    inv = {"paper_kind": "study", "contribution": copied,
           "experiments": [{"name": "E1", "what": "a run", "produces": "maps",
                            "uses": [{"name": "X", "kind": "instrument"}],
                            "key_facts": [{"fact": "sampling interval", "value": "5 min", "why": "aliasing"}],
                            "passages": ["p1"]}],
           "claims": [], "independence": [{"shared": "the dataset", "here": "X"}],
           "conditions": [{"dimension": "season", "value": "summer"}], "terms": [],
           "reliability_checks": ["calibration"], "missing": []}
    sv = {"paper_kind": "Study", "studies": ["waves"], "approach": ["measures"],
          "uses": [{"name": "X", "kind": "instrument"}], "conditions": ["summer"]}
    rows = [{"id": "g0-inventory-smith2021cold", "ok": True, "output": inv},
            {"id": "g0-inventory-jones2019gravity", "ok": True, "output": {**inv, "contribution": "own words"}},
            {"id": "g0-survey-smith2021cold", "ok": True, "output": sv},
            {"id": "g0-survey-jones2019gravity", "ok": True, "output": json.dumps(sv)}]
    (rd / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (rd / "jobs.jsonl").write_text("".join(json.dumps(j) + "\n" for j in js))
    b = g0.tally(lib, rd)
    assert b["copied_strings_removed"] == 1
    assert b["survey"]["paper_kind"] == [["study", 2]]
    assert ["sampling interval", 2] in b["inventory"]["key_facts"]
    assert (rd / "bundle.open.json").is_file()
    assert json.loads((rd / "bundle.open.json").read_text())["inventory"]["paper_kind"] == [["study", 1]]
    ev = g0.evidence_text(rd)
    assert copied not in ev and "p1" not in ev and "smith2021cold" not in ev

    sent = []

    def run_claude(cmd, prompt, timeout=600):
        sent.append(prompt)
        out = {k: ([] if v.get("type") == "array" else "" if v.get("type") == "string" else
                   {"definition": "", "index_fields": []}) for k, v in g0.SCHEMA_OUT["properties"].items()}
        return {"structured_output": out}
    monkeypatch.setattr(derived, "run_claude", run_claude)
    g0.synthesize(lib, rd, n_open=5)
    assert g0.drafts(rd)[-1].name == "draft-1.json"
    assert "Gravity waves radiate" in sent[0]                         # the open paper's text
    assert "Cold pools spread beneath" not in sent[0]                 # never the licensed one's


def test_open_papers_refuses_a_licensed_paper(ilib):
    lib = strict(ilib)
    rd = g0.sample(lib, n=5)
    lines = [json.loads(l) for l in (rd / "sample.jsonl").read_text().splitlines()]
    for r in lines:
        r["open"] = True                                              # a stale or tampered sample
    (rd / "sample.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
    with pytest.raises(Exception):
        g0.open_papers(lib, rd, 5, 1000)


def test_gemini_sees_open_papers_inventories_only(ilib, monkeypatch):
    lib = strict(ilib)
    rd = g0.sample(lib, n=5)
    (rd / "bundle.json").write_text('{"x": "ALL-PAPERS-TALLY"}')
    (rd / "bundle.open.json").write_text('{"x": "OPEN-TALLY"}')
    (rd / "inventories.clean.jsonl").write_text(
        json.dumps({"key": "smith2021cold", "contribution": "LICENSED-ONE"}) + "\n"
        + json.dumps({"key": "jones2019gravity", "contribution": "OPEN-ONE"}) + "\n")
    assert "LICENSED-ONE" in g0.evidence_text(rd)
    t = g0.evidence_text(rd, open_only=True)
    assert "OPEN-ONE" in t and "LICENSED-ONE" not in t
    assert "OPEN-TALLY" in t and "ALL-PAPERS-TALLY" not in t


def test_open_only_sample_like_and_the_outside_gate(ilib):
    lib = strict(ilib)
    rd = g0.sample(lib, n=5, open_only=True)
    keys = {json.loads(l)["key"] for l in (rd / "sample.jsonl").read_text().splitlines()}
    assert keys == {"jones2019gravity"}
    js = g0.jobs(lib, rd)
    assert {j["key"] for j in js} == {"jones2019gravity"}            # the survey too: open papers only
    rd2 = g0.sample(lib, label="haiku", like=rd)
    assert (rd2 / "sample.jsonl").read_text() == (rd / "sample.jsonl").read_text()
    assert all(g0.outside_ok(lib, j) for j in js)
    lic = g0.inventory_job(lib, lib.get("smith2021cold"))
    assert not g0.outside_ok(lib, lic)
    (rd2 / "jobs.jsonl").write_text(json.dumps(lic) + "\n")
    with pytest.raises(Exception):
        g0.run_outside(lib, rd2)


def test_strip_limits_for_anthropic():
    from litnav import batch_runner
    s = batch_runner.strip_limits(g0.inventory_schema(["a"]))
    assert "maxItems" not in json.dumps(s) and "enum" in json.dumps(s)


def test_rules_cite_papers_and_numbers_are_checked(tmp_path):
    (tmp_path / "inventory_index.json").write_text(json.dumps({"1": "a2020", "2": "b2021"}))
    out = {"proposed_rules": [
        {"rule": "Runs shorter than 30 days cannot support climate claims.", "inventories": [1, 2, 9]},
        {"rule": "A correlation below 0.3 is weak; two grades.", "inventories": []}]}
    ev = "Paper 1 ran for 30 days. Paper 2 used 3 km."
    res = g0.check_rules(tmp_path, out, ev)
    assert res[0]["papers"] == ["a2020", "b2021"] and res[0]["numbers_not_in_evidence"] == []
    assert res[1]["n_papers"] == 0 and res[1]["numbers_not_in_evidence"] == ["0.3"]
    assert "inventories" in g0.SCHEMA_OUT["properties"]["proposed_rules"]["items"]["required"]
