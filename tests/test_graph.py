from litnav import graph, index

from conftest import BIB
from fakes import FakeClient


def bibfor(key, doi):
    return BIB.replace("smith2021cold", key).replace("10.1175/JAS-D-20-0001.1", doi)


def test_graph(lib):
    lib.create_item("a2020x", bibfor("a2020x", "10.1/a"))
    lib.create_item("b2020x", bibfor("b2020x", "10.1/b"))
    lib.create_item("c2020x", bibfor("c2020x", "10.1/c"), sharing_level="confidential")
    w = lambda i, refs, lic=None, where="primary_location": {
        "id": f"https://openalex.org/{i}", "referenced_works": [f"https://openalex.org/{r}" for r in refs],
        where: {"license": lic} if lic else None}
    c = FakeClient({
        "doi:10.1/a": w("W1", ["W2", "W9", "W8"], "cc-by"),
        # a CC repository copy records the licence but does not open the item
        "doi:10.1/b": w("W2", ["W9", "W8"], "cc-by", "best_oa_location"),
        "filter": {"results": [{"id": "https://openalex.org/W9", "title": "Outside", "publication_year": 1990,
                                "authorships": [{"author": {"display_name": "X Y"}}]}]},
    })
    r = graph.build(lib, c, min_cites=2, log=lambda *a: None)
    assert r["edges"] == 1 and r["resolved"] == 2
    assert not any("10.1/c" in x for x in c.calls)           # confidential never looked up
    a = lib.get("a2020x")
    assert a.meta["ids"]["openalex"] == "W1" and a.sharing == "open"
    assert a.meta["files"]["sharing_source"] == "openalex-cc"
    b = lib.get("b2020x")
    assert b.sharing == "licensed" and b.meta["files"]["licence"] == "cc-by"
    con = index.connect(lib, readonly=True)
    assert [tuple(x) for x in con.execute("SELECT * FROM edges")] == [("a2020x", "b2020x")]


def test_graph_opens_only_licences_on_the_owners_list(lib):
    lib.create_item("a2020x", bibfor("a2020x", "10.1/a"))
    lib.create_item("b2020x", bibfor("b2020x", "10.1/b"))
    w = lambda i, lic: {"id": f"https://openalex.org/{i}", "referenced_works": [],
                        "primary_location": {"license": lic}}
    c = FakeClient({"doi:10.1/a": w("W1", "cc-by-nc-nd"), "doi:10.1/b": w("W2", "public-domain"),
                    "filter": {"results": []}})
    graph.build(lib, c, min_cites=2, log=lambda *a: None)
    assert lib.get("a2020x").sharing == "licensed"          # NC-ND is not on the default list
    assert lib.get("b2020x").sharing == "open"
    p = lib.root / "library.toml"
    p.write_text(p.read_text() + '\n[licences]\nopen = ["by", "by-nc-nd"]\n')
    from litnav.library import Library
    lib2 = Library(lib.root)
    graph.build(lib2, c, min_cites=2, log=lambda *a: None)
    assert lib2.get("a2020x").sharing == "open"
