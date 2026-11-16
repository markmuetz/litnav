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
    assert a[3] is b[3] and len(embed._CACHE) == 1
    i = a[1].index("smith2021cold")
    assert a[2][i] == "licensed"
    ilib.get("smith2021cold").set_sharing("private")
    index.update(ilib, keys_=["smith2021cold"], log=lambda *x: None)
    c = embed.matrix(index.connect(ilib, readonly=True), model)
    # same passages, so the cached vectors are reused (no 90 MB rebuild for a
    # sharing or tag change) ...
    assert c[3] is a[3] and len(embed._CACHE) == 1
    # ... but the sharing levels are current
    assert {c[2][j] for j, k in enumerate(c[1]) if k == "smith2021cold"} == {"private"}
    # new text means new passages: the cache is rebuilt
    it = ilib.get("jones2019gravity")
    t = it.text()
    assert "Gravity waves radiate" in t
    it.text_path.write_text(t.replace("Gravity waves radiate", "Gravity waves often radiate", 1))
    index.update(ilib, log=lambda *x: None)
    with index.writer(ilib) as con:
        embed.update(ilib, con, fe, log=lambda *a: None)
    assert embed.matrix(index.connect(ilib, readonly=True), model)[3] is not a[3]


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
    """R5: `index --rebuild` works in place, so an open reader stays valid
    and stored vectors are kept."""
    r = index.Reader(ilib)
    c1 = r()
    with index.writer(ilib) as con:
        con.execute("INSERT INTO vectors VALUES ('h', 'm', 1, x'00000000')")
    index.update(ilib, rebuild=True, log=lambda *a: None)
    assert r() is c1 and c1.execute("SELECT count(*) FROM items").fetchone()[0] == 3
    assert c1.execute("SELECT count(*) FROM vectors").fetchone()[0] == 1


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


def test_confidential_dir_spellings_and_tag(lib):
    """M4: `_inbox/confidential/` counts; tagging `confidential` sets the level."""
    from conftest import BIB
    from litnav import importer
    for d in ("confidential", "_Confidential"):
        f = lib.inbox / d / "x.pdf"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"%PDF-1.4")
    assert {t for _, t in importer.inbox_files(lib)} == {importer.CONFIDENTIAL_DIR}
    it = lib.create_item("a2020x", BIB)
    it.add_tag("confidential")
    assert lib.get("a2020x").sharing == "confidential"


def test_config_reloaded(ilib):
    """M3: a long-lived Library follows library.toml."""
    import os
    import time
    assert ilib.licensed_to_models
    p = ilib.root / "library.toml"
    p.write_text(p.read_text().replace("licensed_to_models = true", "licensed_to_models = false"))
    os.utime(p, ns=(time.time_ns() + 10**9,) * 2)
    t = Tools(ilib, purpose=S.SEND_OUT, embedder=None)
    assert "smith2021cold" not in {h["key"] for h in t.search_literature("cold pools", k=50, mode="keyword")}


def test_rename_reads_notes_first(lib):
    """M5: a note with a non-UTF-8 byte is rewritten byte-exactly."""
    from conftest import BIB
    from litnav import rename
    lib.create_item("a2020x", BIB)
    n = lib.root / "notes" / "x.md"
    n.parent.mkdir(exist_ok=True)
    n.write_bytes(b"See [@a2020x] \xff here.\n")
    rename.apply(lib, rename.plan(lib, "a2020x", "b2020y"))
    assert n.read_bytes() == b"See [@b2020y] \xff here.\n"
