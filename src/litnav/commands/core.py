"""init, list, show, tag, share, bib, check, normalise."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .. import bib, keys, sharing
from ..library import Library


def add(sub):
    p = sub.add_parser("init", help="create a new library")
    p.add_argument("dir")
    p.add_argument("--no-git", action="store_true")
    p.add_argument("--licensed-to-models", choices=["yes", "no"],
                   help="may the text of licensed papers be sent to a model? (asked if not given)")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("list", help="list items")
    p.add_argument("--tag", "-t")
    p.add_argument("--sharing")
    p.add_argument("--format", "-f", choices=["table", "keys", "json"], default="table")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show one item")
    p.add_argument("key")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("tag", help="add or remove tags")
    p.add_argument("action", choices=["add", "rm"])
    p.add_argument("tag")
    p.add_argument("keys", nargs="+")
    p.set_defaults(func=cmd_tag)

    p = sub.add_parser("share", help="set sharing level: " + ", ".join(sharing.LEVELS))
    p.add_argument("level", choices=sharing.LEVELS)
    p.add_argument("keys", nargs="+")
    p.set_defaults(func=cmd_share)

    p = sub.add_parser("bib", help="write BibTeX for keys, a tag, or everything")
    p.add_argument("keys", nargs="*")
    p.add_argument("--tag", "-t")
    p.add_argument("--project", "-p")
    p.add_argument("--out", "-o")
    p.set_defaults(func=cmd_bib)

    p = sub.add_parser("check", help="report problems: keys, years, bib, DOIs")
    p.add_argument("--quiet", "-q", action="store_true", help="counts only")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("normalise", help="bring ref.bib files into house style")
    p.add_argument("keys", nargs="*")
    p.add_argument("--dry-run", "-n", action="store_true")
    p.set_defaults(func=cmd_normalise)


def cmd_init(a):
    from ..init import ask_licensed_to_models, init
    if a.licensed_to_models:
        ok = a.licensed_to_models == "yes"
    elif sys.stdin.isatty():
        ok = ask_licensed_to_models()
    else:
        ok = True
        print("note: licensed papers' text may be sent to a model (no terminal to ask); "
              "set licensed_to_models = false in library.toml to change this")
    init(Path(a.dir), git=not a.no_git, licensed_to_models=ok)
    print("licensed papers' text " + ("may" if ok else "will not") + " be sent to a model "
          "(library.toml: licensed_to_models)")


def cmd_list(a):
    lib = Library.find(a.library)
    items = [it for it in lib.items(tag=a.tag) if not a.sharing or it.sharing == a.sharing]
    if a.format == "keys":
        for it in items:
            print(it.key)
    elif a.format == "json":
        json.dump([it.as_dict() for it in items], sys.stdout, indent=1, default=str)
        print()
    else:
        for it in items:
            au = it.authors[0].split(",")[0] if it.authors else ""
            print(f"{it.key:40s} {it.year:>4s}  {au[:18]:18s} {it.title[:70]}")
        print(f"({len(items)} items)", file=sys.stderr)


def cmd_show(a):
    lib = Library.find(a.library)
    it = lib.get(a.key)
    d = it.as_dict()
    if a.json:
        print(json.dumps(d, indent=1, default=str))
        return
    print(f"{it.key}\n  {it.title}\n  {'; '.join(it.authors)}\n  {it.journal} {it.year}")
    if it.doi:
        print(f"  doi: {it.doi}")
    print(f"  sharing: {it.sharing}   tags: {', '.join(it.tags) or '-'}")
    print(f"  pdf: {'yes' if it.has_pdf else 'no'}   text: {'yes' if it.has_text else 'no'}")
    for r, v in it.meta.get("relations", {}).items():
        print(f"  {r}: {v}")
    if d.get("summary"):
        print(f"\n{d['summary']}")


def cmd_tag(a):
    lib = Library.find(a.library)
    for k in a.keys:
        it = lib.get(k)
        (it.add_tag if a.action == "add" else it.remove_tag)(a.tag)


def cmd_share(a):
    lib = Library.find(a.library)
    for k in a.keys:
        lib.get(k).set_sharing(a.level)


def select(lib, keys_=(), tag=None, project=None):
    if keys_:
        return [lib.get(k) for k in keys_]
    if project:
        from ..projects import project_keys
        return [lib.get(k) for k in project_keys(lib, project)]
    return list(lib.items(tag=tag))


def cmd_bib(a):
    lib = Library.find(a.library)
    out = []
    for it in select(lib, a.keys, a.tag, a.project):
        if it.bib_path.is_file():
            out.append(it.bib_path.read_text("utf-8").strip() + "\n")
    text = "\n".join(out)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {len(out)} entries to {a.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


def check_item(it) -> list[str]:
    probs = [f"key: {p}" for p in keys.validate(it.key)]
    if not it.bib_path.is_file():
        probs.append("no ref.bib")
    elif it.entry is None:
        probs.append("ref.bib unparseable")
    else:
        if it.entry.key != it.key:
            probs.append(f"ref.bib key is {it.entry.key!r}")
        if not keys.plausible_year(it.year):
            probs.append(f"implausible year {it.year!r}")
        if not it.doi and it.entry.type == "article":
            probs.append("article without a DOI")
    if it.meta.get("files", {}).get("pdf", True) and not it.has_pdf:
        probs.append("PDF missing")
    if it.sharing not in sharing.LEVELS:
        probs.append(f"unknown sharing level {it.sharing!r}")
    return probs


def cmd_check(a):
    lib = Library.find(a.library)
    from collections import Counter
    c, n = Counter(), 0
    for it in lib.items():
        n += 1
        for p in check_item(it):
            c[p.split(":")[0] if p.startswith(("key:", "ref.bib key")) else p.split(" '")[0]] += 1
            if not a.quiet:
                print(f"{it.key}: {p}")
    print(f"checked {n} items", file=sys.stderr)
    for k, v in c.most_common():
        print(f"  {v:5d}  {k}", file=sys.stderr)
    return 1 if c else 0


def cmd_normalise(a):
    lib = Library.find(a.library)
    items = [lib.get(k) for k in a.keys] if a.keys else list(lib.items())
    changed = skipped = 0
    for it in items:
        if not it.bib_path.is_file():
            continue
        t = it.bib_path.read_text("utf-8", "replace")
        new = bib.normalise(t, lib.authority)
        if new is None:
            skipped += 1
            print(f"  skip (unparseable or would lose a value): {it.key}", file=sys.stderr)
        elif new != t:
            changed += 1
            if not a.dry_run:
                it.write_bib(new)
    print(f"normalise: {changed} {'would change' if a.dry_run else 'changed'}, "
          f"{skipped} skipped, {len(items)} checked", file=sys.stderr)
