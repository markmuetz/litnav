"""guard (a Claude Code hook), licences, use, git-setup, git-check."""
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

    p = sub.add_parser("git-setup", help="keep papers' text out of git: write litnav's .gitignore "
                       "block (from track_text), install the pre-commit check, and list tracked files "
                       "that shouldn't be")
    p.set_defaults(func=cmd_git_setup)

    p = sub.add_parser("git-check", help="pre-commit check: refuse staged paper text, figure crops "
                       "and PDFs that may not be committed (--all: check every tracked file)")
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_git_check)

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


def cmd_git_setup(a):
    from .. import githygiene as G
    lib = Library.find(a.library)
    print(("updated" if G.sync_gitignore(lib) else "unchanged") + " .gitignore (litnav block; track_text = "
          + str(G.track_text(lib)).lower() + ")")
    if not (lib.root / ".git").exists():
        print("not a git repo: no hook installed")
        return
    print(G.install_hook(lib))
    bad = G.problems(lib, G.tracked(lib.root))
    if bad:
        print(f"{len(bad)} tracked file(s) shouldn't be (first 10 below). Untrack them with "
              "`git rm --cached`; they are in the history too, so before pushing anywhere "
              "rewrite it (git filter-repo --invert-paths --path-glob ...):")
        for b in bad[:10]:
            print("  " + b)
        return 1


def cmd_git_check(a):
    from .. import githygiene as G
    lib = Library.find(a.library)
    paths = G.tracked(lib.root) if a.all else G.staged(lib.root)
    bad = G.problems(lib, paths)
    if bad:
        print("litnav git-check: not committing these (no licensed paper text in git):", file=sys.stderr)
        for b in bad[:20]:
            print("  " + b, file=sys.stderr)
        if len(bad) > 20:
            print(f"  ... and {len(bad) - 20} more", file=sys.stderr)
        print("Unstage them (git restore --staged PATH). If one should be allowed (an open paper), "
              "check its sharing level.", file=sys.stderr)
        return 1
