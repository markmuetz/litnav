"""summarize, themes."""
from __future__ import annotations

import os
import sys

from .. import derived, index
from ..library import Library


def add(sub):
    p = sub.add_parser("summarize", help="write summary.json for papers that lack one "
                       "(Anthropic API, or your Claude Code login)")
    p.add_argument("keys", nargs="*", help="only these papers")
    p.add_argument("--tag", "-t")
    p.add_argument("--limit", type=int)
    p.add_argument("--force", action="store_true", help="redo papers that already have a summary")
    p.add_argument("--stale", action="store_true", help="also redo summaries whose text has changed")
    p.add_argument("--via", choices=["api", "claude-code"],
                   help="default: api if ANTHROPIC_API_KEY is set, else claude-code")
    p.add_argument("--model", help=f"default: {derived.SUMMARY_MODEL} (api), "
                   f"{derived.CLAUDE_CODE_SUMMARY_MODEL} (claude-code)")
    p.add_argument("--dry-run", "-n", action="store_true", help="list what would be summarized")
    p.add_argument("--yes", "-y", action="store_true", help="don't ask before a long claude-code run")
    p.set_defaults(func=cmd_summarize)


def _via(a):
    if a.via:
        return a.via
    try:
        import anthropic  # noqa: F401
        has_sdk = True
    except ImportError:
        has_sdk = False
    return "api" if has_sdk and os.environ.get("ANTHROPIC_API_KEY") else "claude-code"


def cmd_summarize(a):
    lib = Library.find(a.library)
    via = _via(a)
    if via == "api" and derived.state_path(lib).exists():
        written, done = derived.api_collect(lib)
        _reindex(lib, written)
        if not done:
            return
    model = a.model or (derived.SUMMARY_MODEL if via == "api" else derived.CLAUDE_CODE_SUMMARY_MODEL)
    items = derived.select(lib, keys=a.keys, tag=a.tag, force=a.force, stale=a.stale, limit=a.limit)
    if not items:
        print("Nothing to summarize.")
        return
    print(f"{len(items)} paper(s) to summarize via {via} with {model}")
    if a.dry_run:
        for it in items:
            print(f"  {it.key}")
        return
    if via == "claude-code":
        mins = max(1, round(len(items) * 10 / 60))
        print("Claude Code summarizes one paper at a time, so it is slower than the Batch API: "
              f"about 5-20 s a paper, roughly {mins} min for these. It counts against your Claude "
              "Code plan's usage. Safe to interrupt: finished summaries are kept.")
        if not a.yes and sys.stdin.isatty() and input("Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
            return
        written = derived.claude_code_summarize(lib, items, model=model)
    else:
        written = derived.api_submit(lib, items, model=model)
    _reindex(lib, written)


def _reindex(lib, written):
    """Summaries feed paper search and the paper vectors: update both."""
    if not written:
        return
    from .. import embed
    index.update(lib, keys_=written, log=lambda *x: None)
    msg = f"{len(written)} summaries written; index updated"
    if embed.available():
        con = index.connect(lib)
        try:
            embed.update(lib, con, embed.Embedder(lib.config["search"]["embedding_model"]),
                         log=lambda *x: None)
        finally:
            con.close()
        msg += " and embedded"
    print(msg)
