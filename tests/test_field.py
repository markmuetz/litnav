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
