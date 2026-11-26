"""A Claude Code PreToolUse hook: keep paper text away from the agent.

litnav's own commands check sharing levels before text goes to a model, but
an agent with a shell can read papers/<key>/text.txt or run pdftotext on a
PDF directly. This hook refuses that: Read, Grep and Bash calls that touch a
paper's text or PDF, unless that paper may be sent to a model (`send_out`
under the library's policy: open papers, or licensed ones when
licensed_to_models = true). Bibliographic files (ref.bib, meta.toml) and
model-written summaries stay readable, and `litnav ...` commands pass: they
apply the same rules themselves.

Three tiers for shell commands that touch a protected file:
- allowed: commands that show names, sizes or hashes, never text (META), and
  moving or deleting files within the library (MANAGE);
- deny: anything that could read a known paper that may not go to a model
  (licensed or confidential): reading it (READERS, recursive searches, the Read
  and Grep tools), copying or moving it out of the library, or a command the
  guard can't classify. Never "ask": the user's licences decide, not a prompt;
- ask: the same for a file whose sharing the guard can't tell (a PDF in the
  PDF folder with no library item: the inbox, _variants/, a stray file). The
  user approves or refuses it in Claude Code's permission prompt, which the
  agent can't answer.

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
OPEN_FILES = {"ref.bib", "meta.toml", "summary.json", "abstract.json"}
RECURSIVE = {"rg", "grep", "egrep", "fgrep", "ag", "ack", "find", "fd", "tar", "zip", "rsync", "cp",
             "scp", "pdftotext", "pdfgrep", "xargs"}
# show names, sizes, dates or hashes, never a file's words
META = {"ls", "stat", "cmp", "shasum", "sha256sum", "sha1sum", "md5", "md5sum", "du", "test", "[", "file",
        "realpath", "basename", "dirname"}
# file management: no text is shown; moving a protected file out of the library asks the user
MANAGE = {"mv", "rm", "mkdir", "rmdir", "touch", "cp"}
# known to print a file's content (or to search it): denied for protected files
READERS = {"cat", "head", "tail", "less", "more", "bat", "tac", "nl", "pdftotext", "pdftoppm", "pdfinfo",
           "pdfimages", "pdftohtml", "pdfgrep", "mutool", "qpdf", "strings", "grep", "egrep", "fgrep",
           "rg", "ag", "ack", "sed", "awk", "cut", "sort", "uniq", "diff", "xxd", "od", "hexdump", "base64",
           "tr", "wc", "find", "fd", "tar", "zip", "unzip", "gzip", "rsync", "scp", "xargs", "open",
           "xdg-open", "tee", "split", "iconv", "jq", "column", "fold", "fmt", "paste", "join", "comm",
           "ugrep", "pandoc", "textutil", "mdls", "convert", "magick", "gs", "tesseract", "curl", "nc"}
SPLIT = re.compile(r"(\|\||&&|[;|&\n])")
SAFE_LEAD = {"litnav"}
# Opening a PDF in the user's viewer shows it to them, not to the agent: allowed,
# but only bare (no --stdout/--stdin redirection, no other app than a viewer).
VIEWERS = {"Preview", "Skim", "PDF Expert", "Adobe Acrobat Reader", "Zotero"}


def _viewer_only(argv: list[str]) -> bool:
    lead = os.path.basename(argv[0])
    if lead == "xdg-open":
        return len(argv) == 2 and argv[1].lower().endswith(".pdf")
    if lead != "open":
        return False
    args, i = argv[1:], 0
    files = []
    while i < len(args):
        a = args[i]
        if a == "-a" and i + 1 < len(args) and args[i + 1] in VIEWERS:
            i += 2
            continue
        if a.startswith("-") or not a.lower().endswith(".pdf"):
            return False
        files.append(a)
        i += 1
    return bool(files)


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

    def check(self, tool: str, inp: dict, cwd: str | None = None) -> tuple[str, str] | None:
        """("deny" | "ask", reason), or None to let the call through."""
        base = Path(cwd or os.getcwd())
        deny = lambda r: ("deny", r) if r else None       # noqa: E731
        if tool == "Read":
            f = inp.get("file_path")
            return deny(self.path_reason(base / os.path.expanduser(f))) if f else None
        if tool == "Grep":
            f = os.path.expanduser(inp.get("path") or str(base))
            return deny(self.path_reason(base / f, recursive=True))
        if tool == "Bash":
            return self.bash_reason(inp.get("command", ""), base)
        return None

    def known_protected(self, p: Path) -> bool:
        """Is `p` a file of a known paper that may not go to a model (not an unknown file)?"""
        p = _real(p)
        if _inside(p, self.papers) and p != self.papers:
            k = p.relative_to(self.papers).parts[0]
        elif _inside(p, self.pdfs) and p != self.pdfs and len(p.relative_to(self.pdfs).parts) == 1:
            k = p.stem
        else:
            return False
        k2 = self.lib.resolve(k)
        return bool(k2) and not self.lib.get(k2).allowed(S.SEND_OUT)

    def _level(self, paths: list[Path], reason: str) -> str:
        return "deny" if any(self.known_protected(q) for q in paths) else "ask"

    def _in_library(self, p: Path) -> bool:
        p = _real(p)
        return any(_inside(p, z) for z in self.zones)

    def _manage(self, lead: str, paths: list[Path], recursive: bool) -> tuple[str, str] | None:
        """mv/cp: a protected source going to a destination outside the library asks the user.
        rm, mkdir, rmdir, touch show nothing and pass."""
        if lead not in ("mv", "cp") or len(paths) < 2:
            return None
        dest, srcs = paths[-1], paths[:-1]
        if self._in_library(dest):
            return None
        for src in srcs:
            r = self.path_reason(src, recursive=recursive or lead == "mv")
            if r:
                return (self._level([src], r), f"{lead} would take a protected file out of the library ({r})")
        return None

    def bash_reason(self, command: str, base: Path) -> tuple[str, str] | None:
        parts = SPLIT.split(command)
        sep = ""
        asked = None
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
            if _viewer_only(argv) and not piped:
                continue
            if lead in META and not (lead == "cmp" and any(
                    a in ("--verbose", "--print-bytes") or re.match(r"^-\w*[lb]", a) for a in argv[1:])):
                continue                               # cmp -l / -b print the bytes themselves
            recursive = lead in RECURSIVE and (lead not in ("grep", "egrep", "fgrep", "cp")
                                               or any(re.match(r"^-\w*[rR]", a) for a in argv[1:]))
            paths, reason = [], None
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
                    paths.append(base / c)
                    if "/" not in c and not (base / c).exists() and c != ".":
                        continue
                    reason = reason or self.path_reason(base / c, recursive=recursive)
            if lead in MANAGE:
                got = self._manage(lead, paths, recursive)
                if got and got[0] == "deny":
                    return got
                asked = asked or got
                continue
            if reason:
                if lead in READERS or piped or self._level(paths, reason) == "deny":
                    return ("deny", reason)
                asked = asked or ("ask", f"{lead} is not a command the guard knows, and it touches a "
                                         f"file whose sharing the guard can't tell ({reason})")
            if recursive and len(argv) >= 1 and lead in ("rg", "ag", "ack", "fd", "find") and \
                    not (piped and lead in ("rg", "ag", "ack")) and \
                    not any(not a.startswith("-") for a in argv[2:]):
                r = self.path_reason(base, recursive=True)       # searches the cwd
                if r:
                    return ("deny", r)
        return asked


def hook(stdin_text: str, explicit_library=None) -> dict | None:
    """Claude Code hook protocol: a deny or ask decision, or None to let it through."""
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
    got = Guard(lib).check(ev.get("tool_name", ""), ev.get("tool_input") or {}, ev.get("cwd"))
    if not got:
        return None
    decision, reason = got
    if decision == "ask":
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "ask",
            "permissionDecisionReason": (
                f"litnav guard: {reason}. The user decides: approve only if no paper text that may "
                "not go to a model will be read.")}}
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"litnav guard: {reason}. Paper text that may not go to a model is not read directly. "
            "Use `litnav search` (it withholds such text), `litnav verify KEY \"claim\"` (a local "
            "model checks it and reports a verdict and location), the paper's ref.bib or "
            "summary.json, or ask the user.")}}

