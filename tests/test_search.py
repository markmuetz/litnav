import hashlib

import numpy as np
import pytest

from litnav import embed, index, search
from litnav import sharing as S

from conftest import BIB
from test_chunks import paper


class FakeEmbedder:
    """Bag of words hashed into 64 dims: similar words -> similar vectors."""
    name = "fake"

    def _v(self, t):
        v = np.zeros(64, dtype="float32")
        for w in t.lower().split():
            v[int(hashlib.md5(w.strip('.,').encode()).hexdigest(), 16) % 64] += 1
        return v

    def docs(self, texts):
        return np.stack([self._v(t) for t in texts])

    def query(self, t):
        return self._v(t)


@pytest.fixture
def ilib(lib):
    for key, level, text in [
        ("smith2021cold", "licensed", paper(3)),
        ("jones2019gravity", "open", paper(2).replace("Cold pools spread beneath convective systems",
                                                     "Gravity waves radiate from WSR-88D observed squall lines")),
        ("doe2026plans_draft", "confidential", paper(2)),
    ]:
        it = lib.create_item(key, BIB, sharing_level=level)
        it.text_path.write_text(text)
    index.update(lib, log=lambda *a: None)
    return lib


def test_index_incremental(ilib):
    s = index.update(ilib, log=lambda *a: None)
    assert s["indexed"] == 0 and s["unchanged"] == 3
    ilib.get("smith2021cold").add_tag("x")
    assert index.update(ilib, log=lambda *a: None)["indexed"] == 1


def test_confidential_text_never_indexed(ilib):
    con = index.connect(ilib, readonly=True)
    assert con.execute("SELECT count(*) FROM chunks WHERE key='doe2026plans_draft'").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM items WHERE key='doe2026plans_draft'").fetchone()[0] == 1


def test_keyword_and_purpose(ilib):
    con = index.connect(ilib, readonly=True)
    hits = search.keyword(con, "cold pools", k=50)
    assert {h["key"] for h in hits} == {"smith2021cold", "jones2019gravity"}
    hits = search.keyword(con, "cold pools", k=50, purpose=S.SERVE_TEXT)
    assert {h["key"] for h in hits} == {"jones2019gravity"}


def test_fts_operators_neutralised(ilib):
    con = index.connect(ilib, readonly=True)
    for q in ['cold AND (pools', 'NOT -x:y', 'WSR-88D', '"unclosed', 'NEAR']:
        search.keyword(con, q)          # must not raise
    hits = search.keyword(con, '"WSR-88D"', k=50)
    assert {h["key"] for h in hits} == {"jones2019gravity"}


def test_required_phrase(ilib):
    con = index.connect(ilib, readonly=True)
    hits = search.keyword(con, '"gravity waves" pools', k=50)
    assert hits and all("Gravity waves" in h["text"] for h in hits)


def test_semantic_and_hybrid(ilib):
    con = index.connect(ilib)
    e = FakeEmbedder()
    embed.update(ilib, con, e, log=lambda *a: None)
    sem = search.semantic(con, "gravity waves squall lines", e, k=5)
    assert sem[0]["key"] == "jones2019gravity"
    hy = search.hybrid(con, '"WSR-88D" radiate', e, k=5)
    assert hy and all(h["key"] == "jones2019gravity" for h in hy)
    assert not any(h["key"] == "doe2026plans_draft" for h in search.semantic(con, "cold pools", e, k=50))
    # resumable / incremental: nothing new to embed
    assert embed.update(ilib, con, e, log=lambda *a: None)["chunks"] == 0


def test_papers_and_similar(ilib):
    con = index.connect(ilib)
    e = FakeEmbedder()
    embed.update(ilib, con, e, log=lambda *a: None)
    ps = search.papers(con, "gravity waves", embedder=e, k=5)
    assert ps[0]["key"] == "jones2019gravity"
    sim = search.similar(con, "smith2021cold", "fake", k=5)
    assert [s["key"] for s in sim][0] == "jones2019gravity"   # locally, a confidential title is matchable
    sim = search.similar(con, "smith2021cold", "fake", k=5, purpose=S.SEND_OUT)
    assert "doe2026plans_draft" not in [s["key"] for s in sim]


def test_filters(ilib):
    con = index.connect(ilib, readonly=True)
    f = search.Filters(keys=["jones2019gravity"])
    assert {h["key"] for h in search.keyword(con, "cold pools", filters=f, k=50)} == {"jones2019gravity"}
    f = search.Filters(year_from=2030)
    assert search.keyword(con, "cold pools", filters=f) == []
