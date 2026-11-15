"""check-refs, convert-refs, backlinks, rename, graph."""
from __future__ import annotations

import difflib
import sys

from .. import refs
from ..library import Library
from ..util import atomic_write


def add(sub):
    p = sub.add_parser("check-refs", help="list [@key] references that resolve to no item")
    p.set_defaults(func=cmd_check_refs)

    p = sub.add_parser("convert-refs", help="turn `key` into [@key] in notes where key is a real item")
    p.add_argument("files", nargs="*", help="default: all Markdown in the library")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("backlinks", help="which notes cite a paper")
    p.add_argument("key")
    p.set_defaults(func=cmd_backlinks)

    p = sub.add_parser("rename", help="rename a key; the old key stays as an alias")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("graph", help="build the citation network from OpenAlex")
    p.add_argument("--min-cites", type=int, default=3)
    p.set_defaults(func=cmd_graph)


def cmd_check_refs(a):
    lib = Library.find(a.library)
    bad = refs.check(lib)
    for f, r in bad:
        print(f"{f.relative_to(lib.root)}:{r.line}: @{r.key}")
    print(f"{len(bad)} unresolved", file=sys.stderr)
    return 1 if bad else 0


def cmd_convert(a):
    from pathlib import Path
    lib = Library.find(a.library)
    files = [Path(f) for f in a.files] if a.files else refs.md_files(lib)
    total = 0
    for f in files:
        t = f.read_text("utf-8")
        new, n = refs.convert_backticks(t, lib.resolve)
        if not n:
            continue
        total += n
        sys.stdout.writelines(difflib.unified_diff(t.splitlines(True), new.splitlines(True),
                                                   str(f), str(f), n=0))
        if a.apply:
            atomic_write(f, new)
    print(f"{total} references {'converted' if a.apply else 'to convert (--apply)'}", file=sys.stderr)


def cmd_backlinks(a):
    lib = Library.find(a.library)
    k = lib.get(a.key).key
    for f in refs.backlinks(lib).get(k, []):
        print(f)


def cmd_rename(a):
    from .. import rename
    lib = Library.find(a.library)
    p = rename.plan(lib, a.old, a.new)
    print(f"rename {p['old']} -> {p['new']}")
    for w in p["warnings"]:
        print(f"  warning: {w}")
    for s, d in p["files"]:
        print(f"  move {s.name} -> {d.name}")
    for f, n in p["notes"]:
        print(f"  rewrite {n} reference(s) in {f.relative_to(lib.root)}")
    for pr in p["projects"]:
        print(f"  project {pr}")
    for k in p["relations"]:
        print(f"  relation in {k}")
    if a.apply:
        rename.apply(lib, p)
        print("done; the old key is kept as an alias")
    else:
        print("dry run; --apply to rename")


def cmd_graph(a):
    from .. import graph
    from ..net import Client
    lib = Library.find(a.library)
    r = graph.build(lib, Client.for_library(lib), min_cites=a.min_cites,
                    log=lambda m: print(m, file=sys.stderr))
    print(r)
