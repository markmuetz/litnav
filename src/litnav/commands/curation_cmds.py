"""project, draft."""
from __future__ import annotations

import sys

from .. import projects
from ..library import Library


def add(sub):
    p = sub.add_parser("project", help="projects: list, show, add, rm, set (projects/<P>/papers.toml)")
    ps = p.add_subparsers(dest="action", metavar="<action>")
    ps.add_parser("list", help="list projects with paper counts")
    x = ps.add_parser("show", help="a project's papers, status and project tags")
    x.add_argument("name")
    x.add_argument("--status")
    x.add_argument("--tag", "-t")
    x = ps.add_parser("add", help="add papers to a project")
    x.add_argument("name")
    x.add_argument("keys", nargs="+")
    x.add_argument("--status")
    x.add_argument("--tag", "-t", action="append", default=[])
    x.add_argument("--note")
    x.add_argument("--create", action="store_true", help="start the project if it does not exist")
    x = ps.add_parser("rm", help="remove papers from a project (the papers stay in the library)")
    x.add_argument("name")
    x.add_argument("keys", nargs="+")
    x = ps.add_parser("set", help="set status, project tags or note for papers in a project")
    x.add_argument("name")
    x.add_argument("keys", nargs="+")
    x.add_argument("--status", help='e.g. "to read", "reading", "read"; "" clears it')
    x.add_argument("--tag", "-t", action="append", default=[])
    x.add_argument("--untag", action="append", default=[])
    x.add_argument("--note", help='"" clears it')
    p.set_defaults(func=cmd_project)

    p = sub.add_parser("draft", help="co-authored drafts: record a new version (never reads the PDF)")
    ds = p.add_subparsers(dest="action", metavar="<action>")
    x = ds.add_parser("version", help="file a new version: the current PDF moves to _variants/, "
                      "the new one takes its place, and curation/drafts.tsv gets a row")
    x.add_argument("key")
    x.add_argument("pdf", help="the new version's PDF (moved, not copied)")
    x.add_argument("--version", dest="ver", help="the version's own date or label (default: received)")
    x.add_argument("--received", help="YYYY-MM-DD (default: today)")
    x.add_argument("--label", help="added to the old version's file name, e.g. submitted, r1")
    x.add_argument("--source", default="pdf", help="pdf, gdoc, overleaf, word ...")
    x.add_argument("--link", default="", help="where the canonical text lives")
    x.add_argument("--note", default="")
    x.add_argument("--apply", action="store_true")
    ds.add_parser("list", help="the version log, curation/drafts.tsv")
    p.set_defaults(func=cmd_draft)


def cmd_project(a):
    lib = Library.find(a.library)
    if a.action in (None, "list"):
        for n in projects.names(lib):
            print(f"{n:24s} {len(projects.load(lib, n)):4d} papers")
        return
    if a.action == "show":
        for p in projects.load(lib, a.name):
            if a.status and p.get("status", "") != a.status:
                continue
            if a.tag and a.tag not in p.get("tags", []):
                continue
            print(f"{p['key']:36s} {p.get('status', ''):12s} {','.join(p.get('tags', [])):24s} "
                  f"{p.get('note', '')[:60]}")
        return
    if a.action == "add":
        done = projects.add(lib, a.name, a.keys, status=a.status, tags=a.tag, note=a.note,
                            create=a.create)
        print(f"added {len(done)} to {a.name}" + (f": {' '.join(done)}" if done else ""),
              file=sys.stderr)
    elif a.action == "rm":
        done = projects.remove(lib, a.name, a.keys)
        print(f"removed {len(done)} from {a.name}", file=sys.stderr)
    elif a.action == "set":
        projects.update(lib, a.name, a.keys, status=a.status, tags=a.tag, untags=a.untag,
                        note=a.note)


def cmd_draft(a):
    from .. import drafts
    lib = Library.find(a.library)
    if a.action in (None, "list"):
        print(drafts.log_path(lib).read_text("utf-8") if drafts.log_path(lib).is_file() else "(none)")
        return
    plan = drafts.plan_version(lib, a.key, a.pdf, received=a.received, version=a.ver,
                               label=a.label, source=a.source, link=a.link, note=a.note)
    for line in plan.describe():
        print(line, file=sys.stderr)
    if not a.apply:
        print("dry run; --apply to do it", file=sys.stderr)
        return
    drafts.apply(lib, plan)
    print("done", file=sys.stderr)
