"""Citation network from OpenAlex reference lists.

For every item with a DOI whose sharing allows lookups: resolve its OpenAlex
work (id cached in meta.toml [ids]), take its reference list, and keep
  - internal edges: library paper -> library paper it cites;
  - outside works cited by >= min_cites library papers: candidates to fetch.
An explicit Creative Commons licence reported by OpenAlex is recorded in
[files] licence and, if the item's sharing level was never set by hand,
makes it `open`. Writes graph.json beside the index and the edges table.
"""
from __future__ import annotations

import json
from collections import Counter

from . import index, licences
from . import sharing as S
from .sources import openalex
from .util import atomic_write


def short(oa_id: str) -> str:
    return (oa_id or "").rsplit("/", 1)[-1]


def build(lib, client, *, min_cites: int = 3, log=print) -> dict:
    opens = licences.open_codes(lib)       # the owner's list, as `litnav licences` uses
    items = [it for it in lib.items(S.LOOKUP) if it.doi]
    works, refs_of = {}, {}
    for n, it in enumerate(items, 1):
        w = openalex.by_doi(client, it.doi, items=[it])
        if n % 100 == 0:
            log(f"  resolved {n}/{len(items)}")
        if not w:
            continue
        wid = short(w.get("id"))
        works[it.key] = w
        refs_of[it.key] = [short(r) for r in w.get("referenced_works") or []]
        ids = it.meta.setdefault("ids", {})
        changed = ids.get("openalex") != wid
        ids["openalex"] = wid
        lic = openalex.cc_licence(w)
        if lic and it.meta.get("files", {}).get("licence") != lic:
            it.meta.setdefault("files", {})["licence"] = lic
            changed = True
        # Only the publisher's version's licence may open an item up, and only
        # an item still at the default `licensed` level (never a level the
        # user chose, nor a stricter library default). The source is recorded.
        prim = openalex.cc_licence(w, primary_only=True)
        if ("sharing" not in it.meta and it.sharing == "licensed" and prim
                and licences.from_openalex(prim) in opens):
            it.meta["sharing"] = "open"
            it.meta.setdefault("files", {})["sharing_source"] = "openalex-cc"
            changed = True
        if changed:
            it.save_meta()
    id2key = {short(w["id"]): k for k, w in works.items()}
    edges, outside, citers = set(), Counter(), {}
    for k, rs in refs_of.items():
        for r in dict.fromkeys(rs):
            if r in id2key and id2key[r] != k:
                edges.add((k, id2key[r]))
            elif r not in id2key:
                outside[r] += 1
                citers.setdefault(r, []).append(k)
    cand_ids = [r for r, c in outside.most_common() if c >= min_cites]
    cands = []
    for w in openalex.by_ids(client, cand_ids):
        wid = short(w["id"])
        au = (w.get("authorships") or [{}])[0].get("author", {}).get("display_name", "")
        cands.append({"id": wid, "title": w.get("title") or "", "year": w.get("publication_year"),
                      "first_author": au, "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
                      "cited_by_library": outside[wid], "cited_by_count": w.get("cited_by_count"),
                      "citers": sorted(citers.get(wid, []))})
    cands.sort(key=lambda c: -c["cited_by_library"])
    with index.writer(lib) as con:
        con.execute("DELETE FROM edges")
        con.executemany("INSERT OR IGNORE INTO edges VALUES (?,?)", sorted(edges))
    indeg, outdeg = Counter(d for _, d in edges), Counter(s for s, _ in edges)
    nodes = []
    for it in lib.items():
        if it.key not in works and not indeg[it.key] and not outdeg[it.key]:
            continue
        nodes.append({"key": it.key, "title": it.title, "year": it.year,
                      "first_author": it.authors[0].split(",")[0] if it.authors else "",
                      "tags": it.tags, "has_pdf": it.has_pdf, "in": indeg[it.key],
                      "out": outdeg[it.key], "sharing": it.sharing})
    g = {"nodes": nodes, "links": [{"source": s, "target": d} for s, d in sorted(edges)],
         "candidates": cands}
    atomic_write(lib.index_dir / "graph.json", json.dumps(g))
    return {"resolved": len(works), "edges": len(edges), "candidates": len(cands),
            "unresolved": len(items) - len(works)}
