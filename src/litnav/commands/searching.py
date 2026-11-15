"""search, similar."""
from __future__ import annotations

import json
import sys
import textwrap

from .. import index, search
from ..library import Library


def add(sub):
    p = sub.add_parser("search", help='search passages (default) or papers; "quoted phrases" are required')
    p.add_argument("query")
    p.add_argument("-k", type=int, default=10)
    p.add_argument("--mode", choices=["hybrid", "keyword", "semantic"], default="hybrid")
    p.add_argument("--papers", action="store_true", help="rank papers, not passages")
    p.add_argument("--year", help="YYYY or YYYY-YYYY")
    p.add_argument("--tag", "-t")
    p.add_argument("--author", "-a")
    p.add_argument("--project", "-p")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("similar", help="papers most like a paper")
    p.add_argument("key")
    p.add_argument("-k", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_similar)


def filters_from(a, lib) -> search.Filters:
    f = search.Filters(tag=a.tag, author=a.author)
    if a.year:
        lo, _, hi = a.year.partition("-")
        f.year_from = int(lo)
        f.year_to = int(hi) if hi else int(lo)
    if getattr(a, "project", None):
        from ..projects import project_keys
        f.keys = project_keys(lib, a.project)
    return f


_EMB = {}


def embedder_for(lib):
    from .. import embed
    if not embed.available():
        return None
    con = index.connect(lib, readonly=True)
    model = lib.config["search"]["embedding_model"]
    n = con.execute("SELECT count(*) FROM vectors WHERE model=?", (model,)).fetchone()[0]
    con.close()
    if not n:
        return None
    if model not in _EMB:
        _EMB[model] = embed.Embedder(model)
    return _EMB[model]


def cmd_search(a):
    lib = Library.find(a.library)
    con = index.connect(lib, readonly=True)
    emb = None if a.mode == "keyword" else embedder_for(lib)
    if emb is None and a.mode != "keyword":
        print("(keyword only: no embeddings yet; run `litnav index`)", file=sys.stderr)
    f = filters_from(a, lib)
    if a.papers:
        res = search.papers(con, a.query, embedder=emb, k=a.k, filters=f)
    else:
        res = search.search(con, a.query, embedder=emb, mode=a.mode, k=a.k, filters=f)
    if a.json:
        print(json.dumps(res, indent=1))
        return
    for r in res:
        if a.papers:
            print(f"{r['key']:36s} {r['year'] or '':>4}  {r['title'][:80]}")
            if r.get("best_passage"):
                print(textwrap.indent(textwrap.fill(r["best_passage"]["text"][:300], 100), "      "))
        else:
            print(f"[@{r['key']}] {r['title'][:70]} ({r['year']}) § {r['section_title'] or r['kind']}")
            print(f"  id: {r['id']}")
            print(textwrap.indent(textwrap.fill(r["text"][:500], 100), "    "))
        print()


def cmd_similar(a):
    lib = Library.find(a.library)
    con = index.connect(lib, readonly=True)
    key = lib.get(a.key).key
    res = search.similar(con, key, lib.config["search"]["embedding_model"], k=a.k)
    if a.json:
        print(json.dumps(res, indent=1))
        return
    for r in res:
        print(f"{r['score']:.3f}  {r['key']:36s} {r['year'] or '':>4}  {r['title'][:80]}")
