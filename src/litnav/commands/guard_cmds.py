"""guard (a Claude Code hook), licences, use."""
from __future__ import annotations

import json
import sys

from ..library import Library


def add(sub):
    p = sub.add_parser("guard", help="Claude Code PreToolUse hook: refuse direct reads of paper "
                       "text and PDFs that may not go to a model (reads the hook event on stdin)")
    p.set_defaults(func=cmd_guard)

    p = sub.add_parser("licences", help="look up papers' licences (Crossref) and mark CC-licensed "
                       "ones open; dry run unless --apply")
    p.add_argument("keys", nargs="*")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--recheck", action="store_true", help="look up papers already checked")
    p.set_defaults(func=cmd_licences)

    p = sub.add_parser("use", help="make a library your default, for commands run outside it "
                       "(Claude Code, the MCP server, the guard hook)")
    p.add_argument("dir", nargs="?", help="omit to show the current default")
    p.set_defaults(func=cmd_use)


def cmd_guard(a):
    from .. import guard
    out = guard.hook(sys.stdin.read(), explicit_library=a.library)
    if out:
        print(json.dumps(out))
    return 0


def cmd_licences(a):
    from .. import licences
    from ..net import Client
    lib = Library.find(a.library)
    c = licences.run(lib, Client.for_library(lib), apply=a.apply, recheck=a.recheck,
                     keys=a.keys or None, log=lambda m: print(m, file=sys.stderr))
    print("  " + "  ".join(f"{k}={v}" for k, v in c.items()), file=sys.stderr)
    if not a.apply:
        print("dry run; --apply to record the licences and mark the open ones", file=sys.stderr)
    elif c["open"]:
        from .. import index
        index.update(lib, log=lambda *x: None)
        print(f"{c['open']} paper(s) now open; index updated", file=sys.stderr)


def cmd_use(a):
    from .. import config
    if not a.dir:
        d = config.default_library()
        print(d if d else "no default library", file=sys.stderr if not d else sys.stdout)
        return 0 if d else 1
    f = config.set_default_library(a.dir)
    print(f"default library: {config.default_library()} (in {f})", file=sys.stderr)
