import pytest

from litnav import index, reading
from litnav import sharing as S
from litnav.tools import Tools

from test_search import ilib, FakeEmbedder  # noqa: F401


def test_tools_send_out_hides_confidential(ilib):
    t = Tools(ilib, purpose=S.SEND_OUT, embedder=None)
    hits = t.search_literature("cold pools", k=50, mode="keyword")
    assert hits and "doe2026plans_draft" not in {h["key"] for h in hits}
    with pytest.raises(PermissionError):
        t.get_paper("doe2026plans_draft")
    assert "doe2026plans_draft" not in {p["key"] for p in t.list_papers(k=100)}
    c = t.get_chunk(hits[0]["id"])
    assert c["text"] == hits[0]["text"]


def test_tools_serve_meta(ilib):
    t = Tools(ilib, purpose=S.SERVE_META, embedder=None)
    # passages need serve_text: only the open paper's text is served
    hits = t.search_literature("cold pools", k=50, mode="keyword")
    assert {h["key"] for h in hits} == {"jones2019gravity"}
    with pytest.raises(PermissionError):
        t.get_section("smith2021cold", "1")
    assert t.get_paper("smith2021cold")["key"] == "smith2021cold"
    with pytest.raises(PermissionError):
        t.get_note("smith2021cold")


def test_sections_paginated(ilib):
    t = Tools(ilib, purpose=S.SEND_OUT, embedder=None)
    secs = t.list_sections("smith2021cold")
    assert [s["slug"] for s in secs][:3] == ["front", "abstract", "1"]
    s = t.get_section("smith2021cold", "1")
    assert s["text"].startswith("1. Introduction") and s["next_offset"] is None


def test_mcp_server_builds(ilib):
    from litnav.mcp_server import build
    server = build(ilib)
    assert server is not None


def test_reading_and_recall(lib):
    import datetime as dt
    from conftest import BIB
    lib.create_item("a2020x", BIB)
    fri = dt.date(2026, 11, 13)
    reading.add_to_list(lib, "a2020x", "read", today=fri)
    reading.mark_done(lib, "a2020x", "read", today=fri)
    c = reading.load_cards(lib)[0]
    assert c["box"] == 1 and c["next_due"] == dt.date(2026, 11, 16)      # Friday + 1 working day
    assert reading.due(lib, today=dt.date(2026, 11, 16))["recall"]
    c = reading.rate(lib, "a2020x", "easy", today=dt.date(2026, 11, 16))
    assert c["box"] == 2 and c["next_due"] == dt.date(2026, 11, 23)
    c = reading.rate(lib, "a2020x", "medium", today=dt.date(2026, 11, 23))
    assert c["box"] == 2 and c["next_due"] == dt.date(2026, 12, 7)
    c = reading.rate(lib, "a2020x", "forgot", today=dt.date(2026, 12, 7))
    assert c["box"] == 1
    # a month that lands on a weekend moves to Monday
    assert reading.interval(3, dt.date(2026, 1, 31)) == dt.date(2026, 3, 2)
    # scans never join recall
    lib.create_item("b2020x", BIB)
    reading.mark_done(lib, "b2020x", "scan", today=fri)
    assert [c["id"] for c in reading.load_cards(lib)] == ["a2020x"]


def test_licensed_to_models_false(ilib):
    """The owner said no at init: licensed text never reaches a model, but
    metadata does; open papers and local use are unaffected."""
    p = ilib.root / "library.toml"
    p.write_text(p.read_text().replace("licensed_to_models = true", "licensed_to_models = false"))
    from litnav.library import Library
    lib = Library(ilib.root)
    assert lib.purpose(S.SEND_OUT) == S.SEND_OUT_STRICT
    t = Tools(lib, purpose=S.SEND_OUT, embedder=None)
    hits = t.search_literature("cold pools", mode="keyword", k=25)
    # the licensed paper is found, with its text withheld; the confidential draft is not
    assert {h["key"] for h in hits} == {"jones2019gravity", "smith2021cold"}
    assert all(h["text_withheld"] for h in hits if h["key"] == "smith2021cold")
    assert not any(h.get("text_withheld") for h in hits if h["key"] == "jones2019gravity")
    paper = t.get_paper("smith2021cold")
    # the abstract is the paper's words; a summary is model-written (send_derived) and may go
    assert paper["title"] and "abstract" not in paper
    if lib.get("smith2021cold").summary():
        assert "summary" in paper
    sec = t.list_sections("smith2021cold")[0]["slug"]
    with pytest.raises(PermissionError):
        t.get_section("smith2021cold", sec)
    assert "smith2021cold" in {r["key"] for r in t.list_papers()}
    assert "doe2026plans_draft" not in {r["key"] for r in t.list_papers()}
    assert lib.get("smith2021cold").allowed(S.LOCAL)
    assert t.get_section("jones2019gravity", t.list_sections("jones2019gravity")[0]["slug"])["text"]


def test_init_question(tmp_path):
    from litnav.init import ask_licensed_to_models, init
    from litnav.library import Library
    answers = iter(["maybe", "n"])
    assert ask_licensed_to_models(input_=lambda _: next(answers), log=lambda *a: None) is False
    root = init(tmp_path / "l", git=False, licensed_to_models=False, log=lambda *a: None)
    assert Library(root).licensed_to_models is False
    assert Library(init(tmp_path / "m", git=False, log=lambda *a: None)).licensed_to_models is True


def test_quiz_logs(lib):
    import datetime as dt
    from litnav import quizzes
    lib.create_item("smith2021cold", None)
    p = quizzes.start(lib, "smith2021cold", "quiz", today=dt.date(2026, 11, 16))
    quizzes.start(lib, "smith2021cold", "discussion", today=dt.date(2026, 11, 20))
    s = quizzes.sessions(lib, "smith2021cold")
    assert [(x["date"], x["kind"]) for x in s] == [("2026-11-16", "quiz"), ("2026-11-20", "discussion")]
    assert "| # | question" in s[0]["body"] and "Shaky" in s[1]["body"]
    quizzes.start(lib, "concept:cre", "quiz")
    ids = {r["id"]: r for r in quizzes.listing(lib)}
    assert ids["smith2021cold"]["sessions"] == 2 and ids["smith2021cold"]["last"] == "2026-11-20"
    assert "concept:cre" in ids and p.endswith("smith2021cold.md")
