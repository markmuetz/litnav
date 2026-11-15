from pathlib import Path

import pytest

from litnav import extract, importer
from litnav.identify import clean_doi, marker_for, verify

from fakes import FakeClient, cr_msg

TEXT_A = """Cold Pools and the Propagation of Mesoscale Convective Systems
Jane A. Smith
https://doi.org/10.1175/JAS-D-20-0001.1
Abstract. We show that cold pools control the propagation of mesoscale convective
systems in idealised simulations. """ + "body text " * 50

# page 1 cites someone else's DOI before (and instead of) its own
TEXT_CITED = """Gravity waves ahead of squall lines
B. Jones
As shown by Smith (2021, https://doi.org/10.1175/JAS-D-20-0001.1), cold pools...
""" + "more body " * 50


@pytest.fixture
def pdfs(lib, monkeypatch):
    texts = {}

    def make(name, text, sub=""):
        d = lib.inbox / sub if sub else lib.inbox
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_bytes(b"%PDF-1.4 " + name.encode() + text.encode()[:40])
        texts[p.name] = text
        return p
    monkeypatch.setattr(extract, "head", lambda p, pages=5: (texts[p.name], "text"))
    return make


def client():
    return FakeClient({
        "works/10.1175/jas-d-20-0001.1": {"message": cr_msg(
            "10.1175/JAS-D-20-0001.1", "Cold pools and the propagation of mesoscale convective systems",
            year=2021, online=2020)},
        "works/10.1175/JAS-D-20-0001.1": {"message": cr_msg(
            "10.1175/JAS-D-20-0001.1", "Cold pools and the propagation of mesoscale convective systems",
            year=2021, online=2020)},
        "query.bibliographic": {"message": {"items": [cr_msg(
            "10.1/other", "Gravity waves ahead of squall lines", family="Jones", given="B", year=2019)]}},
    })


def test_doi_import_uses_issue_year(lib, pdfs):
    pdfs("jas-d-20-0001.pdf", TEXT_A)
    rows = importer.plan(lib, importer.inbox_files(lib), client=client(), log=lambda *a: None)
    assert rows[0]["status"] == "ok"
    assert rows[0]["key"] == "smith2021cold"          # issue year, not the 2020 online date
    importer.apply(lib, rows, log=lambda *a: None)
    it = lib.get("smith2021cold")
    assert it.has_pdf and it.doi == "10.1175/jas-d-20-0001.1"
    assert it.year == "2021"
    assert not list(lib.inbox.glob("*.pdf"))           # moved out of the inbox


def test_cited_doi_not_mistaken_for_own(lib, pdfs):
    pdfs("x.pdf", TEXT_CITED)
    rows = importer.plan(lib, importer.inbox_files(lib), client=client(), log=lambda *a: None)
    # the cited DOI fails verification and is skipped; the title query wins
    assert rows[0]["key"] == "jones2019gravity"
    assert rows[0]["via"] == "title"


def test_duplicate_under_new_filename(lib, pdfs):
    pdfs("a.pdf", TEXT_A)
    rows = importer.plan(lib, importer.inbox_files(lib), client=client(), log=lambda *a: None)
    importer.apply(lib, rows, log=lambda *a: None)
    pdfs("renamed-copy.pdf", TEXT_A + " ")             # different bytes, same DOI
    rows = importer.plan(lib, importer.inbox_files(lib), client=client(), log=lambda *a: None)
    assert rows[0]["status"] == "held" and rows[0]["dup_of"] == "smith2021cold"
    importer.apply(lib, rows, log=lambda *a: None)
    assert len(list(lib.variants.glob("smith2021cold.*.pdf"))) == 1
    assert lib.keys() == ["smith2021cold"]


def test_subdirectory_tags_do_not_accumulate(lib, pdfs):
    pdfs("a.pdf", TEXT_A, sub="PROJA")
    pdfs("b.pdf", TEXT_CITED, sub="PROJB")
    rows = importer.plan(lib, importer.inbox_files(lib), client=client(), log=lambda *a: None)
    importer.apply(lib, rows, log=lambda *a: None)
    assert lib.get("smith2021cold").tags == ["PROJA"]
    assert lib.get("jones2019gravity").tags == ["PROJB"]


def test_confidential_never_read_or_looked_up(lib, pdfs, monkeypatch):
    p = pdfs("doe2026plans_draft.pdf", "secret", sub="_confidential")
    monkeypatch.setattr(extract, "head", lambda *a, **k: pytest.fail("confidential file read"))
    c = client()
    rows = importer.plan(lib, importer.inbox_files(lib), client=c, log=lambda *a: None)
    assert c.calls == []
    assert rows[0]["status"] == "ok" and rows[0]["key"] == "doe2026plans_draft"
    importer.apply(lib, rows, log=lambda *a: None)
    it = lib.get("doe2026plans_draft")
    assert it.sharing == "confidential" and not it.has_text and "confidential" in it.tags


def test_confidential_needs_canonical_name(lib, pdfs):
    pdfs("Draft v3 FINAL.pdf", "x", sub="_confidential")
    rows = importer.plan(lib, importer.inbox_files(lib), client=client(), log=lambda *a: None)
    assert rows[0]["status"] == "needs-key"


def test_plan_roundtrip(lib, pdfs):
    pdfs("a.pdf", TEXT_A)
    rows = importer.plan(lib, importer.inbox_files(lib), client=client(), log=lambda *a: None)
    importer.write_plan(lib, rows)
    back = importer.read_plan(lib)
    assert back[0]["bibtex"] == rows[0]["bibtex"]
    importer.apply(lib, back, log=lambda *a: None)
    assert lib.get("smith2021cold").entry.get("year") == "2021"


def test_helpers():
    assert clean_doi("10.1175/JAS-D-20-0001.1.") == "10.1175/JAS-D-20-0001.1"
    assert clean_doi("10.1002/qj.123Received 3 May") == "10.1002/qj.123"
    assert marker_for("smith_preprint_v2") == "preprint"
    assert marker_for("Supporting_Information") == "supporting"
    assert verify("Cold pools and the propagation of convective systems", TEXT_A) == 1.0
    assert verify("Svalbard auroral emissions in winter", TEXT_A) == 0.0
