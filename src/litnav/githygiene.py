"""Keep papers' text out of the library's git history.

A library repo is usually pushed somewhere (a private GitHub repo, say).
Pushing the text of a licensed paper is redistribution, even to a private
repo, and once it is in the history it is hard to remove. So:

- `track_text = false` (library.toml; the default for new libraries) keeps
  every `papers/*/text.txt` out of git: a block litnav manages in
  .gitignore. The text is regenerated from the PDFs by `litnav extract`.
- Figure crops from papers (`notes/figs/<key>_fig*`) are never tracked
  unless the paper is open, and nor are model jobs and replies under
  derived/ (batches, evals, verify and ask-local logs), which hold paper
  text and quotes.
- A pre-commit hook (`litnav git-setup` installs it; `litnav init` does too)
  runs `litnav git-check`, which refuses a commit that adds any of these, or
  a paper's PDF, whatever .gitignore says (`git add -f` would get past it).
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

from . import sharing as S

BEGIN = "# >>> litnav: keep papers' text out of git (track_text = false; `litnav git-setup`)"
END = "# <<< litnav"
TEXT_RULES = ["papers/*/text.txt"]
ALWAYS_RULES = ["notes/figs/*_fig*",          # figure crops from papers
                # model jobs and replies: they hold paper text and quotes
                "derived/batches/", "derived/model-eval/", "derived/paper-eval/",
                "derived/verify.jsonl", "derived/ask-local.jsonl"]
HOOK_MARK = "# litnav git-check"

_FIG = re.compile(r"^notes/figs/(?P<key>[^/]+?)_fig[^/]*$")
_TEXT = re.compile(r"^papers/(?P<key>[^/]+)/text\.txt$")
_PDF = re.compile(r"(?:^|/)(?P<key>[^/]+)\.pdf$", re.I)


def track_text(lib) -> bool:
    return bool(lib.config["library"].get("track_text", True))


def gitignore_block(lib) -> str:
    rules = ([] if track_text(lib) else TEXT_RULES) + ALWAYS_RULES
    return "\n".join([BEGIN, *rules, END]) + "\n"


def sync_gitignore(lib) -> bool:
    """Write litnav's block into .gitignore (replacing an old one). True if changed."""
    p = lib.root / ".gitignore"
    old = p.read_text("utf-8") if p.is_file() else ""
    block = gitignore_block(lib)
    if BEGIN in old and END in old:
        a = old.index(BEGIN)
        b = old.index(END, a) + len(END)
        new = old[:a] + block.rstrip("\n") + old[b:]
    else:
        new = old + ("" if not old or old.endswith("\n") else "\n") + ("\n" if old else "") + block
    if new != old:
        p.write_text(new, encoding="utf-8")
        return True
    return False


def problems(lib, paths: list[str]) -> list[str]:
    """Why each of these repo paths must not be committed (empty: fine)."""
    out = []
    no_text = not track_text(lib)
    for path in paths:
        m = _TEXT.match(path)
        if m:
            it = _item(lib, m["key"])
            if no_text:
                out.append(f"{path}: track_text = false (paper text stays out of git)")
            elif it is not None and it.sharing == "confidential":
                out.append(f"{path}: {it.key} is confidential")
            continue
        m = _FIG.match(path)
        if m:
            it = _item(lib, m["key"])
            if it is None or not it.allowed(S.SERVE_TEXT):
                out.append(f"{path}: a figure from {m['key']}, which isn't open")
            continue
        m = _PDF.search(path)
        if m and _item(lib, m["key"]) is not None:
            it = _item(lib, m["key"])
            if not it.allowed(S.SERVE_TEXT):
                out.append(f"{path}: the PDF of {it.key} ({it.sharing})")
    return out


def _item(lib, key):
    k = lib.resolve(key)
    return lib.get(k) if k else None


def staged(root: Path) -> list[str]:
    r = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
                       cwd=root, capture_output=True, text=True, check=True)
    return [p for p in r.stdout.split("\0") if p]


def tracked(root: Path) -> list[str]:
    r = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True)
    return [p for p in r.stdout.split("\0") if p]


def hook_path(root: Path) -> Path:
    r = subprocess.run(["git", "rev-parse", "--git-path", "hooks/pre-commit"], cwd=root,
                       capture_output=True, text=True, check=True)
    p = Path(r.stdout.strip())
    return p if p.is_absolute() else root / p


def install_hook(lib) -> str:
    """Add `litnav git-check` to the repo's pre-commit hook. Returns what was done."""
    p = hook_path(lib.root)
    cmd = (f"{shlex.quote(sys.executable)} -m litnav.cli --library {shlex.quote(str(lib.root))} "
           f"git-check || exit 1  {HOOK_MARK}")
    if p.is_file():
        s = p.read_text("utf-8")
        if HOOK_MARK in s:
            lines = [cmd if HOOK_MARK in l else l for l in s.splitlines()]
            new = "\n".join(lines) + "\n"
            if new == s:
                return f"pre-commit hook already runs litnav git-check ({p})"
            p.write_text(new, encoding="utf-8")
            return f"updated the litnav line in {p}"
        p.write_text(s.rstrip("\n") + "\n" + cmd + "\n", encoding="utf-8")
        return f"added litnav git-check to the existing hook {p}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\n" + cmd + "\n", encoding="utf-8")
    p.chmod(0o755)
    return f"installed pre-commit hook {p}"
