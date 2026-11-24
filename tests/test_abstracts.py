"""Public abstracts: API lookups, the saved-page fallback, and who sees them."""
import json

import pytest
from fakes import FakeClient

from litnav import abstracts as A
from litnav import cli, guard
from litnav import sharing as S
from litnav.library import Library

ABSTRACT = ("Cold pools spread beneath organized convective systems and lift the air ahead of "
            "them, triggering new cells. We track cold pools in a large-eddy simulation and show "
            "how their collisions set the spacing of new convection.")
BODY = "Introduction. This sentence is the licensed body text and must never be taken. " * 20


def bib(key, title, doi="", extra=""):
    d = f"    doi = {{{doi}}},\n" if doi else ""
    return (f"@article{{{key},\n    title = {{{title}}},\n    author = {{Smith, Jane}},\n"
            f"{d}{extra}    year = {{2021}}\n}}\n")


def strict(lib, extra=""):
    p = lib.root / "library.toml"
    p.write_text(p.read_text().replace("licensed_to_models = true", "licensed_to_models = false")
                 + extra)
    return Library(lib.root)


@pytest.fixture
def alib(lib):
    lib.create_item("smith2021cold", bib("smith2021cold", "Cold pools and convective organization",
                                         "10.1175/JAS-D-20-0001.1"), sharing_level="licensed")
    lib.create_item("smith2021waves", bib("smith2021waves", "Gravity waves from squall lines",
                                          "10.1029/2021GL000002"), sharing_level="open")
    lib.create_item("smith2021rain", bib("smith2021rain", "Rain in a preprint",
                                         extra="    eprint = {2101.01234},\n"),
                    sharing_level="licensed")
    lib.create_item("doe2026plans_draft", bib("doe2026plans_draft", "Plans", "10.9999/secret"),
                    sharing_level="confidential")
    lib.create_item("smith2021old", bib("smith2021old", "An old report"), sharing_level="licensed")
    return lib


def inverted(text):
    inv = {}
    for i, w in enumerate(text.split()):
        inv.setdefault(w, []).append(i)
    return inv


def test_run_fills_from_the_ladder_and_never_looks_up_confidential(alib):
    c = FakeClient({
        "api.openalex.org/works?": {"results": [
            {"doi": "https://doi.org/10.1175/jas-d-20-0001.1",
             "abstract_inverted_index": inverted(ABSTRACT)}]},
        "DOI:10.1029/2021gl000002": {"paperId": "abc", "abstract": None},
        "api.crossref.org/works/10.1029/2021gl000002": {"message": {
            "abstract": f"<jats:title>Abstract</jats:title><jats:p>{ABSTRACT}</jats:p>"}},
        "ARXIV:2101.01234": {"paperId": "def", "abstract": "Rain. " + ABSTRACT},
    })
    n = A.run(alib, c, env={}, log=lambda *a: None)
    assert n["found"] == 3 and n["no-id"] == 1 and n["openalex"] == 1 and n["crossref"] == 1
    assert A.public(alib.get("smith2021cold")) == ABSTRACT
    got = A.read(alib.get("smith2021waves"))
    assert got["abstract"] == ABSTRACT and got["source"] == "crossref"
    assert A.read(alib.get("smith2021rain"))["source"] == "semanticscholar"
    assert all("secret" not in u for u in c.calls)
    assert A.read(alib.get("doe2026plans_draft")) is None
    # done once: not looked up again without --recheck
    c.calls.clear()
    assert A.run(alib, c, env={}, log=lambda *a: None)["found"] == 0 and c.calls == []


def test_a_miss_is_recorded_and_keyed_apis_need_a_key(alib):
    c = FakeClient({"api.openalex.org/works?": {"results": []}})
    A.run(alib, c, keys_={"smith2021cold"}, env={}, log=lambda *a: None)
    rec = A.read(alib.get("smith2021cold"))
    assert rec["abstract"] == "" and rec["tried"] == ["openalex", "semanticscholar", "crossref"]
    ab, src, _, tried = A.lookup_one(FakeClient({}), "10.1016/j.x.2020.1", env={})
    assert "elsevier" not in tried
    c = FakeClient({"api.elsevier.com/content/abstract/doi/10.1016/j.x.2020.1": {
        "abstracts-retrieval-response": {"coredata": {"dc:description": ABSTRACT}}}})
    ab, src, _, _ = A.lookup_one(c, "10.1016/j.x.2020.1", env={"ELSEVIER_API_KEY": "k"})
    assert src == "elsevier" and ab == ABSTRACT


def test_licensed_paper_public_abstract_goes_where_a_summary_may(alib):
    lib = strict(alib)
    A.write(lib.get("smith2021cold"), ABSTRACT, "openalex")
    d = lib.get("smith2021cold").as_dict(lib.purpose(S.SEND_OUT))
    assert d["public_abstract"] == ABSTRACT and d["abstract_source"] == "openalex"
    lib = strict(alib, "\n[abstracts]\nto_models = false\n")
    assert "public_abstract" not in lib.get("smith2021cold").as_dict(lib.purpose(S.SEND_OUT))


def test_guard_lets_abstract_json_be_read(alib):
    lib = strict(alib)
    A.write(lib.get("smith2021cold"), ABSTRACT, "openalex")
    ev = {"tool_name": "Read", "tool_input": {"file_path": str(lib.get("smith2021cold").dir /
                                                                "abstract.json")},
          "cwd": str(lib.root)}
    assert guard.hook(json.dumps(ev), explicit_library=str(lib.root)) is None


PAGE = f"""<html><head><title>Cold pools and convective organization | JAS</title>
<meta name="citation_title" content="Cold pools and convective organization">
<meta name="description" content="Cold pools spread beneath organized convective systems...">
</head><body><h1>Cold pools and convective organization</h1>
<section class="article-section__abstract"><h2>Abstract</h2><p>{ABSTRACT}</p></section>
<section class="article-section__content"><h2>1 Introduction</h2><p>{BODY}</p></section>
</body></html>"""


def test_from_html_takes_only_the_abstract():
    ab, where = A.from_html(PAGE, "Cold pools and convective organization")
    assert ab == ABSTRACT and where == "abstract element"
    assert "licensed body" not in ab


def test_from_html_refuses_another_papers_page_and_a_page_without_one():
    ab, why = A.from_html(PAGE, "Gravity waves from squall lines in the tropics")
    assert ab == "" and "does not match" in why
    ab, why = A.from_html(f"<html><body><p>{BODY}</p></body></html>")
    assert ab == "" and "no abstract" in why


def test_from_html_falls_back_to_meta_tags():
    page = f'<html><head><meta name="dc.description" content="{ABSTRACT}"></head><body></body></html>'
    assert A.from_html(page)[0] == ABSTRACT


def test_cli_from_html_writes_the_abstract(alib, tmp_path, capsys):
    f = tmp_path / "page.html"
    f.write_text(PAGE)
    assert cli.main(["-L", str(alib.root), "abstracts", "smith2021cold", "--from-html", str(f)]) == 0
    assert A.read(alib.get("smith2021cold"))["source"] == "saved-page"
    assert capsys.readouterr().out.strip() == ABSTRACT
    assert cli.main(["-L", str(alib.root), "abstracts", "doe2026plans_draft",
                     "--from-html", str(f)]) != 0
    assert "confidential" in capsys.readouterr().err
    assert A.read(alib.get("doe2026plans_draft")) is None
