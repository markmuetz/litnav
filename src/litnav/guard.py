"""A Claude Code PreToolUse hook: keep paper text away from the agent.

litnav's own commands check sharing levels before text goes to a model, but
an agent with a shell can read papers/<key>/text.txt or run pdftotext on a
PDF directly. This hook refuses that: Read, Grep and Bash calls that touch a
paper's text or PDF, unless that paper may be sent to a model (`send_out`
under the library's policy: open papers, or licensed ones when
licensed_to_models = true). Bibliographic files (ref.bib, meta.toml) and
model-written summaries stay readable, and `litnav ...` commands pass: they
apply the same rules themselves.

It is a guard against accidents, not a sandbox: a shell can always find
another way in (a script, an archive, a copy). Hook config:

    {"hooks": {"PreToolUse": [{"matcher": "Read|Grep|Bash",
      "hooks": [{"type": "command", "command": "litnav guard"}]}]}}
"""
from __future__ import annotations

import glob
import json
import os
import re
import shlex
from pathlib import Path

from . import sharing as S

# files in papers/<key>/ that are metadata or model-written, not the paper's words
OPEN_FILES = {"ref.bib", "meta.toml", "summary.json"}
RECURSIVE = {"rg", "grep", "egrep", "fgrep", "ag", "ack", "find", "fd", "tar", "zip", "rsync", "cp",
             "scp", "du", "pdftotext", "pdfgrep", "xargs"}
SPLIT = re.compile(r"(\|\||&&|[;|&\n])")
SAFE_LEAD = {"litnav"}


def _real(p: Path) -> Path:
    try:
        return p.expanduser().resolve()
    except (OSError, RuntimeError):
        return p.expanduser().absolute()


def _inside(p: Path, d: Path) -> bool:
    try:
        p.relative_to(d)
        return True
    except ValueError:
        return False


class Guard:
    def __init__(self, lib):
        self.lib = lib
        self.papers = _real(lib.papers_dir)
        self.pdfs = _real(lib.pdf_dir)
        self.zones = [self.papers, self.pdfs]

    def _sendable(self, key: str) -> bool:
        k = self.lib.resolve(key)
        return bool(k) and self.lib.get(k).allowed(S.SEND_OUT)

    def path_reason(self, p: Path, *, recursive: bool = False) -> str | None:
        """Why reading `p` is refused, or None."""
        p = _real(p)
        if _inside(p, self.papers) and p != self.papers:
            rel = p.relative_to(self.papers).parts
            if len(rel) == 1:                       # papers/<key>/: reaches text.txt only recursively
                return None if (self._sendable(rel[0]) or not recursive) \
                    else f"papers/{rel[0]}/ holds the paper's text"
            if rel[-1] in OPEN_FILES:
                return None
            return None if self._sendable(rel[0]) else \
                f"papers/{rel[0]}/{rel[-1]} is the text of a paper that may not go to a model"
        if _inside(p, self.pdfs) and p != self.pdfs:
            rel = p.relative_to(self.pdfs).parts
            if len(rel) == 1 and p.suffix.lower() == ".pdf" and self._sendable(p.stem):
                return None
            return f"{p.name} is a PDF in the library's PDF folder"
        if recursive:                       # a folder above the zones: only a recursive read reaches them
            for z in self.zones:
                if _inside(z, p):
                    return (f"{p} contains the library's {'papers' if z == self.papers else 'PDF'} "
                            "folder; a recursive read would reach paper text")
        return None

    # -- tools

    def check(self, tool: str, inp: dict, cwd: str | None = None) -> str | None:
        base = Path(cwd or os.getcwd())
        if tool == "Read":
            f = inp.get("file_path")
            return self.path_reason(base / os.path.expanduser(f)) if f else None
        if tool == "Grep":
            f = os.path.expanduser(inp.get("path") or str(base))
            return self.path_reason(base / f, recursive=True)
        if tool == "Bash":
            return self.bash_reason(inp.get("command", ""), base)
        return None

    def bash_reason(self, command: str, base: Path) -> str | None:
        parts = SPLIT.split(command)
        sep = ""
        for i, seg in enumerate(parts):
            if i % 2:                                  # a separator
                sep = seg
                continue
            piped = sep == "|"                         # reads the previous command's output
            seg = seg.strip()
            if not seg:
                continue
            try:
                argv = shlex.split(seg, comments=True)
            except ValueError:
                argv = seg.split()
            while argv and re.match(r"^\w+=", argv[0]):          # VAR=x cmd
                argv = argv[1:]
            if not argv:
                continue
            lead = os.path.basename(argv[0])
            if lead == "cd" and len(argv) == 2 and not any(c in argv[1] for c in "*?[$`"):
                base = base / os.path.expanduser(argv[1])        # later commands run there
                continue
            if lead in SAFE_LEAD or (lead == "uv" and "litnav" in argv[:4]):
                continue
            recursive = lead in RECURSIVE and (lead not in ("grep", "egrep", "fgrep", "cp")
                                               or any(re.match(r"^-\w*[rR]", a) for a in argv[1:]))
            for tok in argv[1:]:
                tok = tok.split("=", 1)[1] if tok.startswith("-") and "=" in tok else tok
                if tok.startswith("-") or not tok:
                    continue
                tok = os.path.expanduser(os.path.expandvars(tok))
                cands = [tok]
                if any(c in tok for c in "*?["):
                    cands = glob.glob(os.path.expanduser(tok if os.path.isabs(os.path.expanduser(tok))
                                                         else str(base / tok)))[:200] or []
                for c in cands:
                    if "/" not in c and not (base / c).exists() and c != ".":
                        continue
                    r = self.path_reason(base / c, recursive=recursive)
                    if r:
                        return r
            if recursive and len(argv) >= 1 and lead in ("rg", "ag", "ack", "fd", "find") and \
                    not (piped and lead in ("rg", "ag", "ack")) and \
                    not any(not a.startswith("-") for a in argv[2:]):
                r = self.path_reason(base, recursive=True)       # searches the cwd
                if r:
                    return r
        return None


def hook(stdin_text: str, explicit_library=None) -> dict | None:
    """Claude Code hook protocol: a deny decision, or None to let it through."""
    try:
        ev = json.loads(stdin_text or "{}")
    except json.JSONDecodeError:
        return None
    from .config import LibraryNotFound, find_root
    from .library import Library
    try:
        lib = Library(find_root(explicit_library, start=Path(ev["cwd"]) if ev.get("cwd") else None))
    except LibraryNotFound:
        return None                      # no library known: nothing to guard
    reason = Guard(lib).check(ev.get("tool_name", ""), ev.get("tool_input") or {}, ev.get("cwd"))
    if not reason:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"litnav guard: {reason}. Paper text that may not go to a model is not read directly. "
            "Use `litnav search` (it withholds such text), `litnav verify KEY \"claim\"` (a local "
            "model checks it and reports a verdict and location), the paper's ref.bib or "
            "summary.json, or ask the user.")}}

