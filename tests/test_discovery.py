import json

import pytest

from litnav import discovery as D
from litnav.net import Refused

from fakes import FakeClient


def make(lib, key, doi, title, level="licensed"):
    bib = (f"@article{{{key},\n    title = {{{title}}},\n    author = {{Smith, Jane}},\n"
           f"    year = {{2021}},\n    doi = {{{doi}}}\n}}\n")
    return lib.create_item(key, bib, sharing_level=level)


def graph(lib, cands):
    lib.index_dir.mkdir(parents=True, exist_ok=True)
    (lib.index_dir / "graph.json").write_text(json.dumps({"nodes": [], "links": [], "candidates": cands}))


def test_candidates_and_list(lib):
    make(lib, "smith2021cold", "10.1/held", "Cold pools and squall lines")
    graph(lib, [
        {"id": "W1", "doi": "10.1/HELD", "title": "x", "year": 2021, "first_author": "J Smith", "cited_by_library": 9},
        {"id": "W2", "doi": "10.1/new", "title": "Gravity waves", "year": 2000, "first_author": "A Lee", "cited_by_library": 5},
        {"id": "W3", "doi": "", "title": "Cold Pools and Squall-Lines", "year": 2021, "first_author": "J Smith", "cited_by_library": 4},
        {"id": "W4", "doi": "10.1/few", "title": "Rare", "year": 2000, "first_author": "B Ng", "cited_by_library": 2},
    ])
    cs = D.candidates(lib)
    assert [c["id"] for c in cs] == ["W2"]                  # W1 by DOI, W3 by title, W4 too few
    assert D.add(lib, [{"doi": "https://doi.org/10.1/NEW", "title": "Gravity waves", "source": "candidates:5"}]) == 1
    assert D.add(lib, [{"doi": "10.1/new"}]) == 0           # deduped on normalised DOI
    assert D.candidates(lib)[0]["listed"] == "listed"
    D.set_status(lib, ["10.1/new"], "skip")
    assert D.candidates(lib) == [] and len(D.candidates(lib, show_skipped=True)) == 1
    text = D.tofetch_path(lib).read_text()
    assert text.splitlines()[0].split("\t") == D.COLUMNS
    make(lib, "lee2000gravity", "10.1/new", "Gravity waves")
    assert [r["doi"] for r in D.prune(lib)] == ["10.1/new"] and D.load(lib) == []


def test_maybe_held(lib):
    make(lib, "maddox1980mesoscale", "10.1/m", "Mesoscale convective complexes")
    h = D.Holdings(lib)
    assert h.status("", "Meoscale Convective Complexes", "Robert A. Maddox", 1980)[0] == "maybe"


def test_explore_sends_only_lookups(lib):
    make(lib, "smith2021cold", "10.1/held", "Cold pools")
    make(lib, "doe2026plans_draft", "10.1/secret", "Plans", level="confidential")
    rec = {"recommendedPapers": [
        {"title": "Held already", "year": 2021, "externalIds": {"DOI": "10.1/held"}, "authors": [{"name": "J Smith"}]},
        {"title": "New one", "year": 2022, "externalIds": {"DOI": "10.1/rec"}, "authors": [{"name": "C Wu"}]}]}
    c = FakeClient({"forpaper/DOI:10.1/held": rec})
    out = D.explore(lib, c, ["smith2021cold"], log=lambda *a: None)
    assert [o["doi"] for o in out] == ["10.1/rec"] and out[0]["seeds"] == ["smith2021cold"]
    with pytest.raises(Refused):
        D.explore(lib, c, ["doe2026plans_draft"])
