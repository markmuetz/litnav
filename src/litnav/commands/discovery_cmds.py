"""candidates, explore, to-fetch."""
from __future__ import annotations

import json

from .. import discovery as D
from ..library import Library


def add(sub):
    p = sub.add_parser("candidates", help="outside works cited by several library papers "
                       "(from `litnav graph`)")
    p.add_argument("--min-cites", type=int, default=3,
                   help="at least this many citing library papers (graph keeps >= its own --min-cites)")
    p.add_argument("--limit", "-k", type=int, default=30)
    p.add_argument("--skipped", action="store_true", help="include ones marked skip")
    p.add_argument("--add", type=int, metavar="N", help="put the top N on the to-fetch list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_candidates)

    p = sub.add_parser("explore", help="Semantic Scholar recommendations for seed papers "
                       "(sends only the seeds' DOIs)")
    p.add_argument("seeds", nargs="+", help="library keys")
    p.add_argument("--limit", "-k", type=int, default=20)
    p.add_argument("--add", type=int, metavar="N", help="put the top N on the to-fetch list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_explore)

    p = sub.add_parser("field", help="the field around the library: works with their public "
                       "abstracts, kinds and topics (<index>/field/; decisions in discovery/field.tsv)")
    s = p.add_subparsers(dest="fa", metavar="<action>")
    q = s.add_parser("build", help="fetch the backward set: works cited by several library papers")
    q.add_argument("--min-cites", type=int, default=3)
    q.set_defaults(func=cmd_field_build)
    q = s.add_parser("forward", help="fetch the forward set: works citing many library papers")
    q.add_argument("--since", type=int, default=2000)
    q.add_argument("--min-lib", type=int, default=5, help="cites at least this many library papers")
    q.add_argument("--max-seed-cites", type=int, default=2000)
    q.add_argument("--max-works", type=int, default=20000)
    q.set_defaults(func=cmd_field_forward)
    q = s.add_parser("list", help="top field works by score (default: research papers)")
    q.add_argument("--set", dest="set_", choices=["backward", "forward"])
    q.add_argument("--kind", default="research",
                   help="research, review, dataset, model, project, or all")
    q.add_argument("--limit", "-k", type=int, default=30)
    q.add_argument("--decided", action="store_true", help="include ones you decided on")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_field_list)
    q = s.add_parser("topics", help="OpenAlex topics: works in the field vs papers in the library")
    q.add_argument("--limit", "-k", type=int, default=40)
    q.set_defaults(func=cmd_field_topics)
    q = s.add_parser("embed", help="embed field works and library papers locally (title + abstract)")
    q.set_defaults(func=cmd_field_embed)
    q = s.add_parser("search", help="semantic search over field abstracts and library papers")
    q.add_argument("query")
    q.add_argument("--limit", "-k", type=int, default=15)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_field_search)
    q = s.add_parser("map", help="cluster the field and library into topics; least-held first "
                     "(writes derived/field-map.md)")
    q.add_argument("--clusters", "-n", type=int, default=40)
    q.add_argument("--seed", type=int, default=0)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_field_map)
    for name, hlp in (("want", "mark wanted, and add to the to-fetch list"),
                      ("skip", "mark not wanted (not proposed again)"),
                      ("note", "attach a note")):
        q = s.add_parser(name, help=hlp)
        q.add_argument("ids", nargs="+", help="OpenAlex ids (W...) or DOIs")
        q.add_argument("--note", default="")
        q.set_defaults(func=cmd_field_decide, status=name)
    p.set_defaults(func=cmd_field_list, kind="research", limit=30, decided=False, json=False,
                   set_=None)

    p = sub.add_parser("to-fetch", help="the list of papers to get: list, add, skip, open, prune")
    s = p.add_subparsers(dest="tf", metavar="<action>")
    q = s.add_parser("list", help="show the list (default)")
    q.add_argument("--all", action="store_true", help="include skipped rows")
    q.set_defaults(func=cmd_tf_list)
    q = s.add_parser("add", help="add a paper by DOI")
    q.add_argument("doi")
    q.add_argument("--title", default="")
    q.add_argument("--source", default="manual")
    q.add_argument("--note", default="")
    q.set_defaults(func=cmd_tf_add)
    q = s.add_parser("skip", help="mark DOIs as not wanted (kept, so they are not proposed again)")
    q.add_argument("dois", nargs="+")
    q.set_defaults(func=cmd_tf_skip)
    q = s.add_parser("open", help="open the next N unopened DOIs in the browser to download")
    q.add_argument("n", type=int, nargs="?", default=5)
    q.set_defaults(func=cmd_tf_open)
    q = s.add_parser("prune", help="drop rows the library now holds")
    q.set_defaults(func=cmd_tf_prune)
    p.set_defaults(func=cmd_tf_list, all=False)


def _show(rows, why):
    for i, c in enumerate(rows, 1):
        flags = "".join([f"  [maybe held: {c['maybe_held']}]" if c.get("maybe_held") else "",
                         f"  [{c['listed']}]" if c.get("listed") else ""])
        print(f"{i:3d}. {c.get('first_author', '')} {c.get('year') or ''}: {c.get('title', '')}")
        print(f"     {c.get('doi') or '(no DOI)'}  {why(c)}{flags}")


def _to_rows(cs, source):
    return [{"doi": c.get("doi", ""), "title": c.get("title", ""), "first_author": c.get("first_author", ""),
             "year": c.get("year") or "", "source": source(c)} for c in cs]


def cmd_candidates(a):
    lib = Library.find(a.library)
    cs = D.candidates(lib, min_cites=a.min_cites, show_skipped=a.skipped)[:a.limit]
    if not cs:
        print(f"No candidates cited by {a.min_cites}+ library papers that you don't already have.")
        return
    if a.json:
        print(json.dumps(cs, indent=1))
    else:
        _show(cs, lambda c: f"cited by {c['cited_by_library']} library papers"
              + (f" ({', '.join(c['citers'][:4])}{'...' if len(c['citers']) > 4 else ''})"
                 if c.get("citers") else ""))
    if a.add:
        top = [c for c in cs if not c.get("listed")][:a.add]
        n = D.add(lib, _to_rows(top, lambda c: f"candidates:{c['cited_by_library']}"))
        print(f"added {n} to {D.tofetch_path(lib)}")


def cmd_explore(a):
    from ..net import Client
    lib = Library.find(a.library)
    cs = D.explore(lib, Client.for_library(lib), a.seeds, k=a.limit)
    if a.json:
        print(json.dumps(cs, indent=1))
    else:
        _show(cs, lambda c: f"recommended for {', '.join(c['seeds'])}")
    if a.add:
        top = [c for c in cs if not c.get("listed")][:a.add]
        n = D.add(lib, _to_rows(top, lambda c: f"explore:{'+'.join(c['seeds'][:3])}"))
        print(f"added {n} to {D.tofetch_path(lib)}")


def cmd_tf_list(a):
    lib = Library.find(a.library)
    rows = [r for r in D.load(lib) if a.all or r["status"] != "skip"]
    if not rows:
        print("The to-fetch list is empty.")
    for r in rows:
        st = f" [{r['status']}]" if r["status"] else ""
        print(f"{r['doi'] or '(no DOI)':40s} {r['first_author']} {r['year']}: {r['title'][:70]}{st}")


def cmd_tf_add(a):
    lib = Library.find(a.library)
    n = D.add(lib, [{"doi": a.doi, "title": a.title, "source": a.source, "note": a.note}])
    print("added" if n else "already listed")


def cmd_tf_skip(a):
    lib = Library.find(a.library)
    print(f"{D.set_status(lib, a.dois, 'skip')} marked skip")


def cmd_tf_open(a):
    from ..net import open_in_browser
    lib = Library.find(a.library)
    todo = [r for r in D.load(lib) if r["doi"] and not r["status"]][:a.n]
    for r in todo:
        open_in_browser(f"https://doi.org/{r['doi']}")
        print(f"opened {r['doi']}  {r['title'][:70]}")
    D.set_status(lib, [r["doi"] for r in todo], "opened")
    if todo:
        print(f"Save the PDFs into {lib.pdf_dir / '_inbox'} and run `litnav import`.")


def cmd_tf_prune(a):
    lib = Library.find(a.library)
    gone = D.prune(lib)
    print(f"dropped {len(gone)} row(s) now in the library")


def cmd_field_build(a):
    import sys

    from .. import field
    from ..net import Client
    lib = Library.find(a.library)
    r = field.build_backward(lib, Client.for_library(lib), min_cites=a.min_cites,
                             log=lambda m: print(m, file=sys.stderr))
    print(json.dumps(r))


def cmd_field_list(a):
    from .. import field
    lib = Library.find(a.library)
    rows = field.listing(lib, kind=None if a.kind == "all" else a.kind, n=a.limit,
                         show_decided=a.decided, set_=getattr(a, "set_", None))
    if a.json:
        print(json.dumps(rows, indent=1))
        return
    for r in rows:
        held = f"  [maybe held: {r['maybe_held']}]" if r["maybe_held"] else ""
        ab = "" if r["abstract"] else "  (no abstract)"
        print(f"{r['total']:6.2f}  {r['library_citers']:3d}<lib {r['cites_library'] or 0:3d}>lib  "
              f"{r['cited_by_count'] or 0:6d} cites  "
              f"{r['id']}  {r['year']} {r['first_author'][:22]}: {r['title'][:80]}{held}{ab}")


def cmd_field_topics(a):
    from .. import field
    lib = Library.find(a.library)
    print(f"{'field':>6} {'lib':>5}  topic (subfield)")
    for r in field.topics(lib)[:a.limit]:
        print(f"{r['field_works']:6d} {r['library']:5d}  {r['topic']} ({r['subfield']})")


def cmd_field_decide(a):
    from .. import field
    lib = Library.find(a.library)
    n = field.decide(lib, a.ids, a.status, a.note)
    if a.status == "want":
        con = field.connect(lib)
        rows = [dict(r) for r in con.execute(
            f"SELECT doi, title, first_author, year FROM works WHERE id IN "
            f"({','.join('?' * len(a.ids))}) OR doi IN ({','.join('?' * len(a.ids))})",
            [i.rsplit('/', 1)[-1] for i in a.ids] + [D.norm_doi(i) for i in a.ids])]
        con.close()
        D.add(lib, [{**r, "source": "field"} for r in rows if r["doi"]])
    print(f"{n} marked {a.status}")


def _embedder(lib):
    from ..embed import Embedder
    return Embedder(lib.config["search"]["embedding_model"])


def cmd_field_embed(a):
    import sys

    from .. import field
    lib = Library.find(a.library)
    print(json.dumps(field.embed(lib, _embedder(lib), log=lambda m: print(m, file=sys.stderr))))


def cmd_field_search(a):
    from .. import field
    lib = Library.find(a.library)
    rows = field.search(lib, _embedder(lib), a.query, k=a.limit)
    if a.json:
        print(json.dumps(rows, indent=1))
        return
    for r in rows:
        if r["held"]:
            print(f"{r['score']:.3f}  HELD  {r['held']}")
        else:
            print(f"{r['score']:.3f}  {r['id']}  {r['year']} {r['first_author'][:22]}: "
                  f"{r['title'][:90]} ({r['kind']})" + ("" if r["abstract"] else "  (no abstract)"))


def cmd_field_map(a):
    import sys

    from .. import field
    lib = Library.find(a.library)
    cl = field.topic_map(lib, k=a.clusters, seed=a.seed)
    if a.json:
        print(json.dumps(cl, indent=1))
        return
    p = lib.root / "derived" / "field-map.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(field.map_markdown(cl), "utf-8")
    for c in cl:
        print(f"{c['held_share']:4.0%} held  {c['field_works']:4d} field  {c['held']:4d} lib  "
              f"#{c['cluster']:<3d} {', '.join(c['terms'][:6])}")
    print(f"written to {p}", file=sys.stderr)


def cmd_field_forward(a):
    import sys

    from .. import field
    from ..net import Client
    lib = Library.find(a.library)
    r = field.build_forward(lib, Client.for_library(lib), since=a.since, min_lib=a.min_lib,
                            max_seed_cites=a.max_seed_cites, max_works=a.max_works,
                            log=lambda m: print(m, file=sys.stderr))
    print(json.dumps(r))
