"""Regressions for the 2026-11-16 review (docs/review-2026-11-16.md)."""
import pytest

from litnav import embed, index, quizzes
from litnav import sharing as S
from litnav.library import Library
from litnav.tools import Tools

from test_search import ilib, FakeEmbedder  # noqa: F401


def test_share_change_hidden_before_reindex(ilib):
    """R1: a paper made confidential must vanish from model-facing tools at
    once, even though the index still holds its old level."""
    t = Tools(ilib, purpose=S.SEND_OUT, embedder=None)
    assert "smith2021cold" in {h["key"] for h in t.search_literature("cold pools", k=50, mode="keyword")}
    ilib.get("smith2021cold").set_sharing("confidential")       # meta only, no reindex
    assert "smith2021cold" not in {h["key"] for h in t.search_literature("cold pools", k=50, mode="keyword")}
    assert "smith2021cold" not in {h["key"] for h in t.search_literature("cold pools", k=50, papers=True)}
    assert "smith2021cold" not in {p["key"] for p in t.list_papers(k=100)}


def test_cmd_share_updates_index(ilib):
    from litnav.cli import main
    main(["--library", str(ilib.root), "share", "confidential", "smith2021cold"])
    con = index.connect(ilib, readonly=True)
    assert con.execute("SELECT count(*) FROM chunks WHERE key='smith2021cold'").fetchone()[0] == 0
    assert con.execute("SELECT sharing FROM items WHERE key='smith2021cold'").fetchone()[0] == "confidential"


def test_matrix_cache_shared_and_invalidated(ilib):
    """R2: one cache entry per index file, refreshed when the index changes."""
    fe = FakeEmbedder()
    fe.name = model = ilib.config["search"]["embedding_model"]
    with index.writer(ilib) as con:
        embed.update(ilib, con, fe, log=lambda *a: None)
    embed._CACHE.clear()
    a = embed.matrix(index.connect(ilib, readonly=True), model)
    b = embed.matrix(index.connect(ilib, readonly=True), model)
    assert a is b and len(embed._CACHE) == 1
    i = a[1].index("smith2021cold")
    assert a[2][i] == "licensed"
    ilib.get("smith2021cold").set_sharing("private")
    index.update(ilib, keys_=["smith2021cold"], log=lambda *x: None)
    c = embed.matrix(index.connect(ilib, readonly=True), model)
    assert c is not a and len(embed._CACHE) == 1
    assert {c[2][j] for j, k in enumerate(c[1]) if k == "smith2021cold"} == {"private"}


def test_reindex_keeps_vector_hash(ilib):
    """R3: a reindexed chunk points at its vector without a new embed run."""
    model = ilib.config["search"]["embedding_model"]
    con = index.connect(ilib, readonly=True)
    for r in con.execute("SELECT header, text, vhash FROM chunks"):
        assert r["vhash"] == embed.vhash(model, f"{r['header']}\n{r['text']}")


def test_resolve_sees_items_added_elsewhere(ilib):
    """R4: a long-lived Library sees keys created by another process."""
    from conftest import BIB
    ilib.resolve("smith2021cold")
    import os
    import time
    other = Library(ilib.root)
    time.sleep(0.01)
    other.create_item("new2026paper", BIB)
    os.utime(ilib.papers_dir)
    assert ilib.resolve("new2026paper") == "new2026paper"


def test_reader_reopens_after_rebuild(ilib):
    """R5: the shared read connection follows `index --rebuild`."""
    r = index.Reader(ilib)
    c1 = r()
    index.update(ilib, rebuild=True, log=lambda *a: None)
    c2 = r()
    assert c2 is not c1 and c2.execute("SELECT count(*) FROM items").fetchone()[0] == 3


def test_quiz_concept_slug_checked(ilib):
    """R7."""
    with pytest.raises(ValueError):
        quizzes.path(ilib, "concept:../../x")


def test_reading_due_hides_confidential(ilib):
    """R9."""
    from litnav import reading
    reading.add_to_list(ilib, "doe2026plans_draft", "comment")
    reading.add_to_list(ilib, "smith2021cold", "read")
    rows = reading.load_list(ilib)
    for r in rows:
        r["status"] = "reading"
    reading._save(reading.list_path(ilib), "paper", rows)
    d = Tools(ilib, purpose=S.SEND_OUT, embedder=None).reading_due()
    assert d["reading"] == ["smith2021cold"]
    assert all(c["id"] != "doe2026plans_draft" for c in d["recall"])


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX shell script as a fake claude")
def test_ask_claude_code_stdin_and_kill(ilib, tmp_path, monkeypatch):
    """R6: the question goes on stdin (a leading '-' is not an option), and
    closing the stream kills claude."""
    import json
    import os
    import time
    from litnav import ask
    pidf = tmp_path / "pid"
    fake = tmp_path / "claude"
    fake.write_text(f"""#!/bin/sh
echo $$ > {pidf}
q=$(cat)
printf '%s\\n' '{json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Q:"}]}})}'
printf '{{"type":"assistant","message":{{"content":[{{"type":"text","text":"%s"}}]}}}}\\n' "$q"
sleep 30
""")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    g = ask.stream_answer(ilib, "--help me")
    evs = []
    for ev in g:
        evs.append(ev)
        if ev["type"] == "text" and "--help me" in ev["text"]:
            break
    assert any("--help me" in e.get("text", "") for e in evs)
    g.close()                                   # the page went away
    pid = int(pidf.read_text())
    time.sleep(0.2)
    with pytest.raises(OSError):
        os.kill(pid, 0)


def test_related_papers_skips_stale_keys(ilib):
    """R10: an edge to a key renamed since `litnav graph` is skipped."""
    with index.writer(ilib) as con:
        con.execute("INSERT INTO edges VALUES ('smith2021cold', 'gone2020old')")
        con.execute("INSERT INTO edges VALUES ('smith2021cold', 'jones2019gravity')")
    r = Tools(ilib, purpose=S.SEND_OUT, embedder=None).related_papers("smith2021cold", "cites")
    assert r["cites"] == ["jones2019gravity"]
