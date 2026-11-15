"""search, similar, eval."""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

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

    p = sub.add_parser("eval", help="measure search against eval/questions.toml (recall@10, MRR)")
    p.add_argument("--save", action="store_true", help="save the run to eval/runs/<date>-<git>.json")
    p.add_argument("--compare", nargs=2, metavar=("A", "B"), help="compare two saved runs (B against A)")
    p.add_argument("--metric", choices=["mrr", "recall"], default="mrr")
    p.add_argument("--rows", action="store_true", help="show every question's scores")
    p.set_defaults(func=cmd_eval)


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


def cmd_eval(a):
    import json
    from .. import evaluate
    lib = Library.find(a.library)
    if a.compare:
        ra, rb = (json.loads(Path(x).read_text()) for x in a.compare)
        print(evaluate.compare(ra, rb, a.metric))
        return
    res = evaluate.run(lib, embedder_for(lib))
    print(evaluate.report(res))
    if a.rows:
        for r in res["rows"]:
            cells = "  ".join(f"{c}={v[a.metric]:.2f}" for c, v in r.items() if "/" in c)
            print(f"- {r['q'][:60]:60s} {cells}")
    if a.save:
        out = lib.root / "eval" / "runs" / f"{res['date']}-{res['litnav_git'] or 'nogit'}.json"
        evaluate.save(res, out)
        print(f"saved {out}")
