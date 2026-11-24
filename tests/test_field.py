"""The field store: backward set, kinds, decisions."""
import json

from fakes import FakeClient

from litnav import cli, field
from litnav import discovery as D

ABS = ("Cold pools spread beneath organized convective systems and lift the air ahead of them, "
       "triggering new cells; we track them in large-eddy simulations.")


def inv(t):
    d = {}
    for i, w in enumerate(t.split()):
        d.setdefault(w, []).append(i)
    return d


def bib(key, title, doi):
    return f"@article{{{key},\n  title = {{{title}}},\n  author = {{Smith, Jane}},\n  doi = {{{doi}}},\n  year = {{2021}}\n}}\n"


def test_build_backward_kinds_links_and_decisions(lib):
    a = lib.create_item("smith2021cold", bib("smith2021cold", "Cold pools", "10.1/a"))
    a.meta["ids"] = {"openalex": "W1"}
    a.save_meta()
    lib.create_item("dee2011erainterim")                         # held, but no bib
    g = {"candidates": [
        {"id": "W10", "doi": "10.1/r", "title": "Gust fronts and new cells", "year": 2015,
         "first_author": "C Wu", "cited_by_library": 5, "cited_by_count": 80, "citers": ["smith2021cold"]},
        {"id": "W11", "doi": "10.1002/qj.828", "title": "The ERA-Interim reanalysis",
         "year": 2011, "first_author": "D Dee", "cited_by_library": 9, "cited_by_count": 30000,
         "citers": []},
        {"id": "W12", "doi": "10.1/m", "title": "Description of the NCAR Community Atmosphere Model",
         "year": 2010, "first_author": "R Neale", "cited_by_library": 4, "cited_by_count": 900,
         "citers": []},
        {"id": "W13", "doi": "10.1/few", "title": "Cited by too few", "year": 2000,
         "first_author": "X Y", "cited_by_library": 2, "cited_by_count": 9, "citers": []}]}
    (lib.index_dir).mkdir(parents=True, exist_ok=True)
    (lib.index_dir / "graph.json").write_text(json.dumps(g))
    topic = {"display_name": "Convection", "subfield": {"display_name": "Atmospheric Science"},
             "field": {"display_name": "Earth Sciences"}}

    def w(i, title, refs=(), typ="article"):
        return {"id": f"https://openalex.org/{i}", "doi": None, "title": title, "type": typ,
                "publication_year": 2015, "authorships": [{"author": {"display_name": "C Wu"}}],
                "abstract_inverted_index": inv(ABS), "cited_by_count": 80,
                "citation_normalized_percentile": {"value": 0.9}, "primary_topic": topic,
                "referenced_works": [f"https://openalex.org/{r}" for r in refs]}
    c = FakeClient({
        "openalex:W10|W11|W12": {"results": [
            w("W10", "Gust fronts and new cells", ["W1", "W12"]),
            w("W11", "The ERA-Interim reanalysis"),
            w("W12", "Description of the NCAR Community Atmosphere Model")]},
        "openalex:W1\"": {"results": [{"id": "https://openalex.org/W1", "primary_topic": topic}]},
    })
    r = field.build_backward(lib, c, log=lambda *x: None)
    assert r["fetched"] == 3 and r["maybe"] == 1
    assert r["kinds"] == {"research": 1, "dataset": 1, "model": 1}
    top = field.listing(lib, kind="research")
    assert [x["id"] for x in top] == ["W10"] and top[0]["abstract"].startswith("Cold pools")
    assert field.listing(lib, kind="dataset")[0]["maybe_held"] == "dee2011erainterim"
    con = field.connect(lib)
    links = {tuple(x) for x in con.execute("SELECT src, dst FROM links")}
    assert {("W10", "W1"), ("W10", "W12"), ("W1", "W10")} <= links
    assert field.topics(lib)[0] == {"topic": "Convection", "subfield": "Atmospheric Science",
                                    "field_works": 3, "library": 1}
    # decisions: want goes to to-fetch too; decided works leave the list
    assert cli.main(["-L", str(lib.root), "field", "want", "W10"]) == 0
    assert field.decisions(lib)[0]["status"] == "want"
    assert field.listing(lib, kind="research") == []
    assert [r["doi"] for r in D.load(lib)] == []        # W10 has no DOI in this fake


def test_kind_of():
    k = lambda t, **kw: field.kind_of({"title": t, **kw})   # noqa: E731
    assert k("ERA5 global reanalysis") == "dataset"
    assert k("The GPCP version 2.3 monthly analysis") == "dataset"
    assert k("Overview of the Coupled Model Intercomparison Project Phase 6 (CMIP6) "
             "experimental design and organization") == "project"
    assert k("The Met Office Unified Model Global Atmosphere 7.0/7.1 configurations") == "model"
    assert k("Madden-Julian oscillation", type="review") == "review"
    assert k("Cold pools organize convection") == "research"


def test_kind_of_keeps_studies_as_research():
    k = lambda t: field.kind_of({"title": t})   # noqa: E731
    assert k("Tropical Intraseasonal Variability in 14 IPCC AR4 Climate Models. Part I") == "research"
    assert k("MJO and Convectively Coupled Equatorial Waves Simulated by CMIP5 Climate Models") \
        == "research"
    assert k("Global Radiative-Convective Equilibrium in the Community Atmosphere Model, "
             "Version 5") == "research"
    assert k("Mechanisms of convective cloud organization by cold pools over tropical warm "
             "ocean during the AMIE/DYNAMO field campaign") == "research"
    assert k("Can the GPM IMERG Final Product Accurately Represent MCSs") == "research"
    assert k("EUREC4A: A Field Campaign to Elucidate the Couplings Between Clouds") == "project"
    assert k("A new dynamical core for the Met Office's global and regional modelling") == "model"
    assert k("Description of the NCAR Community Atmosphere Model (CAM 3.0)") == "model"


def _store(lib):
    from litnav import index
    a = lib.create_item("smith2021cold", bib("smith2021cold", "Cold pools and gust fronts", "10.1/a"))
    lib.create_item("doe2026plans_draft", bib("doe2026plans_draft", "Secret plans", "10.1/s"),
                    sharing_level="confidential")
    index.update(lib, log=lambda *x: None)
    con = field.connect(lib)
    with con:
        con.execute("INSERT INTO library VALUES ('W1','smith2021cold','','','')")
        con.execute("INSERT INTO library VALUES ('W2','doe2026plans_draft','','','')")
        rows = [("W10", "Cold pools trigger new cells at gust fronts", "cold pools gust fronts cells"),
                ("W11", "Cold pool collisions and gust fronts", "cold pools gust fronts collide"),
                ("W20", "Ozone in the stratosphere", "ozone stratosphere chemistry ozone"),
                ("W21", "Stratospheric ozone recovery", "ozone stratosphere recovery chemistry")]
        for i, t, ab in rows:
            con.execute("INSERT INTO works (id, title, abstract, year, first_author, kind, score, "
                        "library_citers) VALUES (?,?,?,2020,'X','research',1,3)", (i, t, ab))
    con.close()
    return a


def test_embed_search_and_map(lib):
    from test_search import FakeEmbedder
    _store(lib)
    e = FakeEmbedder()
    r = field.embed(lib, e, log=lambda *x: None)
    assert r["total"] == 5                                    # 4 works + 1 library paper, no draft
    assert field.embed(lib, e, log=lambda *x: None)["embedded"] == 0
    hits = field.search(lib, e, "ozone stratosphere", k=2)
    assert {h["id"] for h in hits} == {"W20", "W21"}
    cl = field.topic_map(lib, k=2)
    by = {tuple(sorted(w["id"] for w in c["top_unheld"])): c for c in cl}
    assert by[("W20", "W21")]["held"] == 0 and by[("W10", "W11")]["held"] == 1
    assert cl[0]["held_share"] == 0                           # least held first
    assert "ozone" in by[("W20", "W21")]["terms"]
    assert "# The field around the library" in field.map_markdown(cl)


def test_forward_counts_weighted_citations(lib):
    con = field.connect(lib)
    with con:
        for i, k in (("W1", "a2020x"), ("W2", "b2020x"), ("W3", "c2020x")):
            con.execute("INSERT INTO library VALUES (?,?,'','','')", (i, k))
    con.close()
    def page(ids):
        return {"meta": {}, "results": [{"id": f"https://openalex.org/{i}"} for i in ids]}
    topic = {"display_name": "T", "subfield": {"display_name": "S"}, "field": {"display_name": "F"}}
    c = FakeClient({
        "openalex:W1|W2|W3": {"results": [
            {"id": "https://openalex.org/W1", "cited_by_count": 10},
            {"id": "https://openalex.org/W2", "cited_by_count": 10},
            {"id": "https://openalex.org/W3", "cited_by_count": 50000}]},       # too cited: no seed
        "cites:W1,": page(["W50", "W51"]),
        "cites:W2,": page(["W50"]),
        "openalex:W50\"": {"results": [{"id": "https://openalex.org/W50", "title": "Recent work",
                                        "publication_year": 2022, "abstract_inverted_index": inv(ABS),
                                        "primary_topic": topic, "referenced_works":
                                        ["https://openalex.org/W1", "https://openalex.org/W2"]}]},
    })
    r = field.build_forward(lib, c, min_lib=2, log=lambda *x: None)
    assert r["seeds"] == 2 and r["kept"] == 1 and not any("cites:W3" in u for u in c.calls)
    w = field.listing(lib, set_="forward")[0]
    assert w["id"] == "W50" and w["cites_library"] == 2 and w["fscore"] > 0
    con = field.connect(lib)
    assert {tuple(x) for x in con.execute("SELECT * FROM links")} == {("W50", "W1"), ("W50", "W2")}


def test_mcp_search_field_marks_held_and_hides_confidential(lib):
    from test_search import FakeEmbedder
    from litnav.tools import Tools
    _store(lib)
    e = FakeEmbedder()
    field.embed(lib, e, log=lambda *x: None)
    t = Tools(lib, embedder=e)
    r = t.search_field("cold pools gust fronts", k=5)
    held = [x for x in r["results"] if "held" in x]
    assert held and held[0]["held"] == "smith2021cold"
    assert all("doe2026" not in json.dumps(x) for x in r["results"])
    assert all(x["kind"] == "research" for x in t.search_field("ozone", k=3, kind="research")["results"]
               if "kind" in x)


def test_forward_stops_on_a_network_failure_and_writes_nothing(lib, capsys):
    con = field.connect(lib)
    with con:
        for i, k in (("W1", "a2020x"), ("W2", "b2020x")):
            con.execute("INSERT INTO library VALUES (?,?,'','','')", (i, k))
    con.close()
    c = FakeClient({
        "openalex:W1|W2": {"results": [{"id": "https://openalex.org/W1", "cited_by_count": 10},
                                       {"id": "https://openalex.org/W2", "cited_by_count": 10}]},
        "cites:W1,": {"meta": {}, "results": [{"id": "https://openalex.org/W50"}]},
        # no route for cites:W2: the fake raises NetError, as an offline client does
    })
    import pytest
    with pytest.raises(field.Interrupted, match="seed 2 of 2.*page 1"):
        field.build_forward(lib, c, min_lib=1, log=lambda *x: None)
    assert field.connect(lib).execute("SELECT count(*) FROM works").fetchone()[0] == 0
    import litnav.net as N
    monkey = {"get_json": lambda *a, **k: (_ for _ in ()).throw(N.NetError("no network"))}
    class Down:
        def get_json(self, *a, **k):
            return monkey["get_json"]()
    with pytest.raises(field.Interrupted, match="batch 1 of 1"):
        field.fetch(Down(), ["W1"])



def test_forward_pages_by_number_so_reruns_hit_the_cache(lib):
    con = field.connect(lib)
    with con:
        con.execute("INSERT INTO library VALUES ('W1','a2020x','','','')")
    con.close()
    full = {"meta": {}, "results": [{"id": f"https://openalex.org/W{1000 + i}"} for i in range(200)]}
    c = FakeClient({
        "openalex:W1\"": {"results": [{"id": "https://openalex.org/W1", "cited_by_count": 250}]},
        '"page": 1,': full,
        '"page": 2,': {"meta": {}, "results": [{"id": "https://openalex.org/W9"}]},
        "openalex:W": {"results": []},
    })
    r = field.build_forward(lib, c, min_lib=1, log=lambda *x: None)
    assert r["citing_works"] == 201
    pages = [u for u in c.calls if "cites:W1" in u]
    assert len(pages) == 2 and all("cursor" not in u for u in pages)
