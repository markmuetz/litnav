import json
from types import SimpleNamespace as NS

import pytest

from litnav import derived
from litnav.net import Refused

from test_search import ilib, FakeEmbedder  # noqa: F401

GOOD = {"topic": "Cold pools.", "summary": "They spread.", "keywords": ["cold pools"],
        "key_items": [{"kind": "finding", "statement": "Cold pools spread outward.", "numbers": [], "conditions": "", "hedging": "strong", "chunks": ["c1"]}]}


def test_gate(ilib):
    with pytest.raises(Refused):
        derived.outbound(ilib, [ilib.get("doe2026plans_draft")])
    derived.outbound(ilib, [ilib.get("smith2021cold"), ilib.get("jones2019gravity")])
    keys = {it.key for it in derived.select(ilib)}
    assert keys == {"smith2021cold", "jones2019gravity"}
    with pytest.raises(Refused):
        derived.select(ilib, keys=["doe2026plans_draft"])


def test_gate_licensed_off(ilib):
    p = ilib.root / "library.toml"
    p.write_text(p.read_text().replace("licensed_to_models = true", "licensed_to_models = false"))
    from litnav.library import Library
    lib = Library(ilib.root)
    assert {it.key for it in derived.select(lib)} == {"jones2019gravity"}
    with pytest.raises(Refused, match="licensed_to_models"):
        derived.outbound(lib, [lib.get("smith2021cold")])


def test_write_stable(ilib):
    it = ilib.get("smith2021cold")
    prov = derived.provenance(it, model="m", via="api", chars=10, truncated=False)
    assert derived.write_summary(it, GOOD, prov)
    assert not derived.write_summary(it, GOOD, prov)          # unchanged: no write
    d = json.loads(it.summary_path.read_text())
    assert d["provenance"]["text_sha256"] and "date" not in d["provenance"]
    assert derived.is_current(it, "m") and not derived.is_current(it, "other")
    assert "smith2021cold" not in {i.key for i in derived.select(ilib)}
    it.text_path.write_text(it.text() + "\nMore text.\n")
    assert "smith2021cold" in {i.key for i in derived.select(ilib, stale=True)}


def test_claude_code_route(ilib, monkeypatch):
    calls = []

    def fake(cmd, prompt, timeout=600):
        calls.append((cmd, prompt))
        if "Gravity" in prompt or "gravity" in prompt.lower() and len(calls) == 1:
            raise RuntimeError("boom")
        return {"structured_output": GOOD, "modelUsage": {"claude-haiku-5-5": {}}}
    monkeypatch.setattr(derived, "run_claude", fake)
    items = derived.select(ilib)
    written = derived.claude_code_summarize(ilib, items, log=lambda *a: None)
    assert written
    cmd = calls[0][0]
    for flag in ("--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config",
                 "--json-schema"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    s = ilib.get(written[0]).summary()
    assert s["provenance"]["via"] == "claude-code" and s["provenance"]["model"] == "claude-haiku-5-5"


def test_claude_code_stops_on_auth(ilib, monkeypatch):
    def fake(cmd, prompt, timeout=600):
        raise derived.Stop("Not logged in")
    monkeypatch.setattr(derived, "run_claude", fake)
    logs = []
    assert derived.claude_code_summarize(ilib, derived.select(ilib), log=logs.append) == []
    assert len(logs) == 1 and "stopping" in logs[0]


def test_batch_collect_rechecks_sharing(ilib, monkeypatch):
    it = ilib.get("smith2021cold")
    from litnav.util import sha256_file
    state = {"batch_id": "b1", "model": "claude-haiku-5-5", "items": {
        "p00000": {"key": "smith2021cold", "sha": sha256_file(it.text_path), "chars": 10, "truncated": False},
        "p00001": {"key": "jones2019gravity", "sha": "stale", "chars": 10, "truncated": False}}}
    derived.state_path(ilib).parent.mkdir(parents=True, exist_ok=True)
    derived.state_path(ilib).write_text(json.dumps(state))
    msg = NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps(GOOD))])
    results = [NS(custom_id=c, result=NS(type="succeeded", message=msg)) for c in state["items"]]
    batches = NS(retrieve=lambda bid: NS(id=bid, processing_status="ended"),
                 results=lambda bid: iter(results))
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda: NS(messages=NS(batches=batches)))
    it.set_sharing("confidential")
    written, done = derived.api_collect(ilib, log=lambda *a: None)
    assert done and written == []                   # confidential now; jones's text changed
    assert not derived.state_path(ilib).exists()


def test_themes(ilib, monkeypatch):
    for k in ("smith2021cold", "jones2019gravity", "doe2026plans_draft"):
        ilib.get(k).summary_path.write_text(json.dumps(GOOD))
    keys = [k for k, _ in derived.theme_inputs(ilib)]
    assert sorted(keys) == ["jones2019gravity", "smith2021cold"]       # never the confidential one
    assert derived.keys_in_report("1. X: `a2020b`, [c2021d], [@e2022f]") == {"a2020b", "c2021d", "e2022f"}
    p = derived.themes_prompt(derived.theme_inputs(ilib), "REPORT [@smith2021cold]")
    assert "EXISTING REPORT" in p and "Keep existing themes verbatim" in p
    seen = {}
    monkeypatch.setattr(derived, "themes_claude_code", lambda prompt, model: seen.setdefault("p", prompt) and "# Themes\n")
    derived.themes_path(ilib).parent.mkdir(exist_ok=True)
    derived.themes_path(ilib).write_text("1. Cold pools: [@smith2021cold]\n")
    from litnav.cli import main
    assert main(["-L", str(ilib.root), "themes", "--update", "--via", "claude-code"]) == 0
    assert "jones2019gravity" in seen["p"] and "smith2021cold" not in seen["p"].split("NEW PAPERS")[1]
    assert derived.themes_path(ilib).read_text() == "# Themes\n"


def test_claude_code_parallel_and_stop(ilib, monkeypatch):
    import threading
    import time
    active, peak, lock = [0], [0], threading.Lock()

    def fake(cmd, prompt, timeout=600):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return {"structured_output": GOOD, "modelUsage": {"claude-haiku-5-5": {}}}
    monkeypatch.setattr(derived, "run_claude", fake)
    items = derived.select(ilib)
    written = derived.claude_code_summarize(ilib, items, jobs=5, log=lambda *a: None)
    assert sorted(written) == sorted(i.key for i in items) and peak[0] == len(items)

    calls = []

    def limit(cmd, prompt, timeout=600):
        calls.append(1)
        raise derived.Stop("usage limit reached")
    monkeypatch.setattr(derived, "run_claude", limit)
    logs = []
    derived.claude_code_summarize(ilib, items, jobs=1, log=logs.append)
    assert len(calls) == 1 and logs == ["stopping: usage limit reached"]


def test_copy_check_repairs_and_allows_names():
    paper = ("The convec-\ntion over the Sahel is orga- nised by cold pools that spread across the "
             "region during the afternoon and evening hours in the monsoon season. Dynamics of the Atmospheric "
             "general circulation Modeled On Non-hydrostatic Domains was a model intercomparison. "
             "Precipitation at Zürich falls mostly in summer from deep convective storms over the Alps.")
    g = derived.ngrams(paper)
    # hyphenation repaired: the copy is still found
    copied = "the convection over the sahel is organised by cold pools that spread across the region"
    n, words, shared, total = derived.longest_copy(copied, g)
    assert n == 15 and words.startswith("the convection over") and shared == total
    # accents folded: 'Zurich' matches 'Zürich'
    assert derived.copied_run("precipitation at zurich falls mostly in summer from deep convective storms", g) == 11
    # a name written out before its acronym, and the paper's title, are allowed
    name = ("The Dynamics of the Atmospheric general circulation Modeled On Non-hydrostatic Domains (DYAMOND) "
            "runs differ.")
    assert derived.copied_run(name, g) == 0
    assert derived.allowed_spans("Results from the Coupled Model Intercomparison Project (CMIP) show") == {3, 4, 5, 6}
    title = "Cold pools that spread across the region during the afternoon"
    assert derived.copied_run("cold pools that spread across the region during the afternoon", g, title=title) == 0
    assert derived.copied_run("cold pools that spread across the region during the afternoon", g) == 10
    assert derived.words("Café ΔT_max") == ["cafe", "δt", "max"]


def test_copy_stats_logged(ilib):
    it = ilib.get("smith2021cold")
    first = " ".join(it.text().split()[:8])
    d = {**GOOD, "summary": first + ". Then something entirely different happens here today, unlike before."}
    st = derived.copy_stats(it, d)
    assert 0 < st["shared_6grams"] < 1 and st["copied_run"] < derived.COPY_LIMIT
    prov = derived.provenance(it, model="m", via="api", chars=10, truncated=False)
    derived.write_summary(it, d, prov)
    p = json.loads(it.summary_path.read_text())["provenance"]
    assert p["shared_6grams"] == st["shared_6grams"] and "copied_run" in p


def test_numbers_and_labels_count_once():
    assert derived.words("rain at 2.5–3.0 mm day−1 in 1.5F and E63-SMCM, high-resolution") == [
        "rain", "at", "2.5-3.0", "mm", "day-1", "in", "1.5f", "and", "e63-smcm", "high", "resolution"]
    g = derived.ngrams("The column is anomalously moist at 2.5-3.0 mm day-1 rain rates in this run.")
    assert derived.copied_run("the column is anomalously moist at 2.5–3.0 mm day−1 rain rates", g) == 11
