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


TEXT_MS = """manuscript submitted to Journal of Advances in Modeling Earth Systems (JAMES)
Tracers Computed with a Mixed Online/Offline Method for Process Studies
Ann B. Example, Carl Sample
Key Points: an efficient method for back and forward tracers.""" + " body" * 50

MANUAL = {"title": "Tracers Computed with a Mixed Online/Offline Method "
                   "for Process Studies",
          "author": "Example, Ann B. and Sample, Carl", "year": "2026",
          "journal": "Journal of Advances in Modeling Earth Systems"}


def test_manual_metadata_imports_a_paper_with_no_doi(lib, pdfs):
    f = pdfs("Mixed_Online_Offline.pdf", TEXT_MS)
    c = FakeClient({})
    rows = importer.plan(lib, [(f, "")], manual=MANUAL, client=c, log=lambda *a: None)
    assert c.calls == []                                 # nothing looked up
    assert rows[0]["status"] == "ok" and rows[0]["key"] == "example2026tracers"
    assert rows[0]["via"] == "manual"
    importer.apply(lib, rows, log=lambda *a: None)
    it = lib.get("example2026tracers")
    assert it.has_pdf and it.entry.type == "article" and not it.doi
    assert it.year == "2026" and "Sample" in it.entry.get("author")
    assert it.meta["files"]["identified_via"] == "manual"
    from litnav.commands.core import check_item
    assert "article without a DOI" in check_item(it)     # flagged until a DOI is added
    # the same paper again is held, not a second item
    g = pdfs("again.pdf", TEXT_MS)
    rows = importer.plan(lib, [(g, "")], manual=MANUAL, client=c, log=lambda *a: None)
    assert rows[0]["status"] == "held" and rows[0]["dup_of"] == "example2026tracers"


def test_manual_metadata_checks_the_title_against_the_pdf(lib, pdfs):
    f = pdfs("wrong.pdf", TEXT_A)                        # a different paper
    rows = importer.plan(lib, [(f, "")], manual=MANUAL, key="example2026x",
                         client=FakeClient({}), log=lambda *a: None)
    assert rows[0]["status"] == "low-confidence" and rows[0]["key"] == "example2026x"


def test_manual_metadata_confidential_is_never_read(lib, pdfs, monkeypatch):
    f = pdfs("draft.pdf", TEXT_MS, sub="_confidential")
    monkeypatch.setattr(extract, "head", lambda *a, **k: pytest.fail("read a confidential file"))
    rows = importer.plan(lib, importer.inbox_files(lib), manual=MANUAL,
                         client=FakeClient({}), log=lambda *a: None)
    assert rows[0]["status"] == "ok" and rows[0]["confidential"] == "yes"
    importer.apply(lib, rows, log=lambda *a: None)
    it = lib.get("example2026tracers")
    assert it.sharing == "confidential" and it.entry.type == "article"


def test_manual_metadata_needs_title_author_year():
    with pytest.raises(ValueError, match="--author"):
        importer.check_manual({"title": "T", "author": "", "year": "2026"})
    with pytest.raises(ValueError, match="implausible"):
        importer.check_manual({"title": "T", "author": "A, B", "year": "26"})


def test_cli_no_match_suggests_metadata_by_hand(lib, pdfs, monkeypatch, capsys):
    from litnav import cli
    from litnav.identify import Proposal
    f = pdfs("x.pdf", TEXT_MS)
    monkeypatch.setattr(importer, "identify", lambda *a, **k: Proposal(file="x.pdf", status="no-match"))
    monkeypatch.setattr(importer.Client, "for_library", classmethod(lambda cls, lib: FakeClient({})))
    assert cli.main(["-L", str(lib.root), "import", str(f)]) == 0
    assert "--title T --author" in capsys.readouterr().err
    assert cli.main(["-L", str(lib.root), "import", str(f), "--title", MANUAL["title"],
                     "--author", "Example, Ann B.", "--author", "Sample, Carl",
                     "--year", "2026", "--apply"]) == 0
    it = lib.get("example2026tracers")
    assert it.entry.type == "misc" and "Sample, Carl" in it.entry.get("author")


def test_arxiv_id_found_but_api_down_says_so(lib, pdfs, monkeypatch, capsys):
    from litnav import cli
    from litnav.identify import identify
    f = pdfs("2501.01234v1.pdf", "A preprint with no DOI in its first pages.")
    p = identify(f, FakeClient({}), lambda k: False, cfg=lib.config)
    assert p.status == "no-match" and p.arxiv == "2501.01234"
    monkeypatch.setattr(importer.Client, "for_library", classmethod(lambda cls, lib: FakeClient({})))
    assert cli.main(["-L", str(lib.root), "import", str(f)]) == 0
    err = capsys.readouterr().err
    assert "arXiv id 2501.01234" in err and "did not answer" in err
    assert "--title T --author" not in err


def test_import_fetches_the_abstract_and_reads_the_licence_line(lib, pdfs, monkeypatch):
    from litnav import abstracts
    text = TEXT_A + ("\nThis is an open access article under the terms of the Creative Commons "
                     "Attribution License.\n")
    pdfs("jas-d-20-0001.pdf", text)
    rows = importer.plan(lib, importer.inbox_files(lib), client=client(), log=lambda *a: None)
    inv = {w: [i] for i, w in enumerate(("A public abstract of the paper, long enough to count as "
                                         "one: cold pools lift air and trigger new convective cells "
                                         "ahead of the gust front.").split())}
    c = client()
    c.routes["api.openalex.org/works?"] = {"results": [
        {"doi": "https://doi.org/10.1175/jas-d-20-0001.1", "abstract_inverted_index": inv}]}
    monkeypatch.setattr(extract, "extract_item", lambda it, log=None: it.text_path.write_text(text))
    importer.apply(lib, rows, client=c, log=lambda *a: None)
    it = lib.get("smith2021cold")
    assert abstracts.read(it)["source"] == "openalex"
    assert it.sharing == "open" and it.meta["licence"]["source"] == "text"


def test_a_pdf_already_in_the_folder_is_used_if_the_same_and_left_if_not(lib, pdfs):
    """A PDF already under the new key's name used to stop the whole import after half-making the
    item. The same file is now used; a different one leaves that row (and only it) undone."""
    from litnav.util import sha256_file
    p = pdfs("jas-d-20-0001.pdf", TEXT_A)
    rows = importer.plan(lib, importer.inbox_files(lib), client=client(), log=lambda *a: None)
    lib.pdf_dir.mkdir(parents=True, exist_ok=True)
    (lib.pdf_dir / "smith2021cold.pdf").write_bytes(b"another file")
    msgs = []
    c = importer.apply(lib, rows, log=msgs.append)
    assert c["left"] == 1 and not (lib.root / "papers" / "smith2021cold").exists() and p.exists()
    assert any("differs" in m for m in msgs)
    (lib.pdf_dir / "smith2021cold.pdf").write_bytes(p.read_bytes())
    assert sha256_file(lib.pdf_dir / "smith2021cold.pdf") == rows[0]["sha256"]
    c = importer.apply(lib, rows, log=lambda *a: None)
    assert c["imported"] == 1 and lib.get("smith2021cold").has_pdf
    assert not p.exists()                               # the inbox copy, a duplicate, is gone
