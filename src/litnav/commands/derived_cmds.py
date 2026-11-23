"""summarize, check-summaries, themes."""
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
    p.add_argument("--via", choices=["api", "claude-code", "local", "queue"],
                   help="api, claude-code (outside models: only papers whose text may be sent out), "
                        "local ([local_model] in library.toml, e.g. Ollama), or queue (add to the "
                        "batch for a bigger model: `litnav queue export`). Default: local if "
                        "[local_model] is set, else api if ANTHROPIC_API_KEY is set, else claude-code")
    p.add_argument("--model", help=f"default: {derived.SUMMARY_MODEL} (api), "
                   f"{derived.CLAUDE_CODE_SUMMARY_MODEL} (claude-code)")
    p.add_argument("--max-chars", type=int, help="local/queue: characters of each paper the model reads "
                   "(default [local_model] max_chars, 90000; 0 = the whole paper, for a long-context "
                   "model such as the cluster's)")
    p.add_argument("--dry-run", "-n", action="store_true", help="list what would be summarized")
    p.add_argument("--yes", "-y", action="store_true", help="don't ask before a long claude-code run")
    p.add_argument("--jobs", "-j", type=int, default=1,
                   help="claude-code: papers at a time (e.g. 5); uses your plan's usage that much faster")
    p.set_defaults(func=cmd_summarize)
    p = sub.add_parser("check-summaries", help="the local model checks each key item of each summary "
                       "against the paper, in a fresh context (verify); the verdict goes into the item")
    p.add_argument("keys", nargs="*", help="only these papers (default: every summary with key items)")
    p.add_argument("--tag", "-t")
    p.add_argument("--limit", type=int, help="at most this many papers")
    p.add_argument("--force", action="store_true", help="re-check items already checked")
    p.add_argument("--via", choices=["local", "queue"], default="local",
                   help="local: now, with [local_model]; queue: add to the batch for the cluster")
    p.add_argument("--dry-run", "-n", action="store_true")
    p.set_defaults(func=cmd_check_summaries)
    add_themes(sub)


def cmd_check_summaries(a):
    from .. import localmodel, sharing as S
    lib = Library.find(a.library)
    items = [lib.get(k) for k in a.keys] if a.keys else list(lib.items(purpose=S.LOCAL_MODEL, tag=a.tag))
    jobs, papers = [], 0
    for it in items:
        if not it.allowed(S.LOCAL_MODEL) or not it.has_text:
            continue
        js = localmodel.item_check_jobs(lib, it, force=a.force)
        if js:
            papers += 1
            jobs += js
            if a.limit and papers >= a.limit:
                break
    if not jobs:
        print("Nothing to check.")
        return
    if a.dry_run:
        print(f"{len(jobs)} key item(s) to check in {papers} paper(s)")
        return
    if a.via == "queue":
        n = localmodel.enqueue(lib, jobs)
        print(f"queued {n} check(s) ({len(jobs) - n} already queued); `litnav queue push` sends them")
        return
    st = localmodel.run_now(lib, jobs, log=lambda m: print(m, file=sys.stderr))
    bad = [r for r in st if r.get("record", {}).get("verdict") in ("contradicted", "not_found")]
    print(f"checked {sum(r['status'] == 'ok' for r in st)} item(s); {len(bad)} contradicted or not found")


def _via(a, lib=None):
    if a.via:
        return a.via
    if lib is not None and lib.local_model.get("url"):
        return "local"
    try:
        import anthropic  # noqa: F401
        has_sdk = True
    except ImportError:
        has_sdk = False
    return "api" if has_sdk and os.environ.get("ANTHROPIC_API_KEY") else "claude-code"


def _summarize_local(lib, a, via):
    from .. import localmodel, sharing as S
    items = derived.select(lib, keys=a.keys, tag=a.tag, force=a.force, stale=a.stale, limit=a.limit,
                           purpose=S.LOCAL_MODEL)
    if not items:
        print("Nothing to summarize.")
        return
    if a.dry_run:
        print(f"{len(items)} paper(s) to summarize via {via}")
        for it in items:
            print(f"  {it.key}")
        return
    limit = 10 ** 9 if a.max_chars == 0 else a.max_chars
    jobs = [localmodel.summarize_job(it, limit) for it in items]
    if via == "queue":
        n = localmodel.enqueue(lib, jobs)
        print(f"queued {n} summary job(s) ({len(jobs) - n} already queued); "
              "`litnav queue export DIR` for a batch run")
        return
    url, model, _ = localmodel.endpoint(lib)
    print(f"{len(items)} paper(s) to summarize with {model} at {url}. Safe to interrupt: "
          "finished summaries are kept.", file=sys.stderr)
    st = localmodel.run_now(lib, jobs, log=lambda m: print(m, file=sys.stderr))
    _reindex(lib, [s["key"] for s in st if s["status"] == "ok"])


def cmd_summarize(a):
    lib = Library.find(a.library)
    via = _via(a, lib)
    if via in ("local", "queue"):
        return _summarize_local(lib, a, via)
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
        mins = max(1, round(len(items) * 10 / 60 / max(1, a.jobs)))
        print("Claude Code summarizes one paper at a time, so it is slower than the Batch API: "
              f"about 5-20 s a paper, roughly {mins} min for these. It counts against your Claude "
              "Code plan's usage. Safe to interrupt: finished summaries are kept.")
        if not a.yes and sys.stdin.isatty() and input("Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
            return
        written = derived.claude_code_summarize(lib, items, model=model, jobs=a.jobs)
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


def add_themes(sub):
    p = sub.add_parser("themes", help="theme report across all summaries (derived/themes.md)")
    p.add_argument("--update", "-u", action="store_true",
                   help="fold in only papers the report doesn't cite; existing themes stay as they are")
    p.add_argument("--tag", "-t")
    p.add_argument("--via", choices=["api", "claude-code"])
    p.add_argument("--model")
    p.add_argument("--dry-run", "-n", action="store_true", help="say what would be sent, then stop")
    p.set_defaults(func=cmd_themes)


def cmd_themes(a):
    from ..util import atomic_write
    lib = Library.find(a.library)
    out = derived.themes_path(lib)
    summaries = derived.theme_inputs(lib, a.tag)
    if not summaries:
        print("No summaries to work from: run `litnav summarize` first.")
        return
    report = None
    if a.update and out.is_file():
        report = out.read_text("utf-8")
        # "Uncited" is not "new": a full run leaves some papers out, and those come back here.
        cited = derived.keys_in_report(report)
        summaries = [(k, s) for k, s in summaries if k not in cited]
        if not summaries:
            print(f"{out} already cites every summarized paper.")
            return
        print(f"updating {out} with {len(summaries)} uncited paper(s)")
    else:
        print(f"deriving themes from {len(summaries)} summaries")
    prompt = derived.themes_prompt(summaries, report)
    via = _via(a)
    model = a.model or (derived.THEMES_MODEL if via == "api" else derived.CLAUDE_CODE_THEMES_MODEL)
    print(f"{len(prompt):,} characters to {model} via {via}")
    if a.dry_run:
        return
    text = derived.themes_api(prompt, model) if via == "api" else derived.themes_claude_code(prompt, model)
    if not text.strip():
        raise ValueError("the model returned nothing; the report was not changed")
    atomic_write(out, text.rstrip() + "\n")
    print(f"wrote {out}")
