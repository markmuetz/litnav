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
