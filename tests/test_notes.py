"""Search over the user's own notes, and each note's sharing level."""
from litnav import embed, index, notes, search
from litnav import sharing as S
from litnav.tools import Tools

from test_search import FakeEmbedder, ilib  # noqa: F401


def write(lib, rel, text):
    p = lib.root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def setup_notes(lib, to_models=True):
    if to_models:
        cfg = lib.root / "library.toml"
        cfg.write_text(cfg.read_text() + "\n[notes]\nto_models = true\n")
    write(lib, "topics/outflows.md", "# Outflow boundaries\n\n## Gaps\n\nNobody has tracked "
          "outflow boundaries in kilometre-scale runs. See [@smith2021cold].\n")
    write(lib, "topics/secret.md", "# Plans\n\nOutflow boundaries in `doe2026plans_draft`.\n")
    write(lib, "topics/review.md", "---\nsharing: confidential\n---\n# Review\n\nOutflow "
          "boundaries, for a referee report.\n")
    write(lib, "concepts/cre.md", "# Cloud radiative effect\n\nOutflow boundaries aside, CRE is ...\n")
    write(lib, "notes/smith2021cold.md", "# smith2021cold\n\n## Gist\n\nOutflow boundaries spread.\n")
    write(lib, "notes/unknown2026thing.md", "# x\n\nOutflow boundaries in a draft.\n")
    write(lib, "notes/_template.md", "# <key>\n\nOutflow boundaries template.\n")
    index.update(lib, log=lambda *a: None)


def test_levels(ilib):
    setup_notes(ilib)
    lv = {r: notes.level(ilib, r) for r in ("topics/outflows.md", "topics/secret.md",
                                             "topics/review.md", "concepts/cre.md",
                                             "notes/smith2021cold.md", "notes/unknown2026thing.md")}
    assert lv == {"topics/outflows.md": "private", "topics/secret.md": "confidential",
                  "topics/review.md": "confidential", "concepts/cre.md": "private",
                  "notes/smith2021cold.md": "private", "notes/unknown2026thing.md": "confidential"}


def test_default_keeps_own_writing_local(ilib):
    setup_notes(ilib, to_models=False)
    assert notes.level(ilib, "topics/outflows.md") == "confidential"
    assert notes.level(ilib, "notes/smith2021cold.md") == "private"


def test_search_notes_local_and_sent(ilib):
    setup_notes(ilib)
    con = index.connect(ilib, readonly=True)
    local = {r["path"] for r in search.notes(con, "outflow boundaries", k=20)}
    assert {"topics/secret.md", "topics/review.md", "notes/unknown2026thing.md"} <= local
    assert "notes/_template.md" not in local
    sent = {r["path"] for r in Tools(ilib, purpose=S.SEND_OUT, embedder=None)
            .search_notes("outflow boundaries", k=20)}
    assert sent == {"topics/outflows.md", "concepts/cre.md", "notes/smith2021cold.md"}
    r = Tools(ilib, purpose=S.SEND_OUT, embedder=None).search_notes("smith gist", k=5, kind="note")
    assert r and r[0]["about"] == "smith2021cold" and r[0]["section"] == "Gist"


def test_live_check_after_share(ilib):
    """A topic note citing a paper made confidential after indexing is withheld at once."""
    setup_notes(ilib)
    it = ilib.get("smith2021cold")
    it.meta["sharing"] = "confidential"
    it.save_meta()                      # no reindex
    sent = {r["path"] for r in Tools(ilib, purpose=S.SEND_OUT, embedder=None)
            .search_notes("outflow boundaries", k=20)}
    assert "topics/outflows.md" not in sent and "notes/smith2021cold.md" not in sent


def test_semantic_notes(ilib):
    setup_notes(ilib)
    con = index.connect(ilib)
    e = FakeEmbedder()
    embed.update(ilib, con, e, log=lambda *a: None)
    r = search.notes(con, "cloud radiative effect", embedder=e, k=3, purpose=S.SEND_OUT)
    assert r[0]["path"] == "concepts/cre.md"
    assert all(x["path"] != "topics/review.md" for x in
               search.notes(con, "referee report", embedder=e, k=20, purpose=S.SEND_OUT))
