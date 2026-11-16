"""The user's own writing, made searchable: notes/, concepts/, topics/.

Each Markdown file is split at its headings into passages, which go in the
index's `notes` table (keyword and semantic search, like paper passages).
Passage IDs are `<path>#<section>#<hash8>`, e.g. `topics/mcs.md#gaps#1a2b3c4d`.

Sharing. A note is the user's own text, but it can be about a confidential
paper, so each file gets a level, worked out every time it is indexed or
returned to a model:

  1. a `sharing: <level>` line in front matter (`---` at the top) wins;
  2. a note that cites a confidential item ([@key], or a key in backticks)
     is confidential;
  3. notes/<key>.md about a library item: confidential if the item is,
     otherwise private (as `get_note` has always done);
  4. notes/<name>.md whose name is not a library item: confidential. It may be
     about something deliberately kept out of the library, such as a draft;
  5. other notes (concepts/, topics/, notes not about one paper): private if
     library.toml has `[notes] to_models = true`, otherwise confidential,
     i.e. searchable on this machine but never sent to a model. This is the
     owner's choice; the default keeps them local.

Write `sharing: confidential` in front matter for a note that must stay on
the machine although it cites nothing confidential, e.g. one written for a
manuscript you are refereeing, which is never in the library.

`private` allows sending to a model for the owner (Ask, MCP) but never serving
to anyone else.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from . import refs
from . import sharing as S

DIRS = {"notes": "note", "concepts": "concept", "topics": "topic"}
MAX_CHARS = 1500
HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$", re.M)
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
TICKED = re.compile(r"`([A-Za-z][A-Za-z0-9_]*\d{4}[A-Za-z0-9_]*)`")    # keys in backticks


@dataclass
class Passage:
    id: str
    path: str          # relative to the library root, with forward slashes
    kind: str          # note, concept, topic
    about: str         # the item a notes/<key>.md file is about, else ""
    title: str         # the file's first heading, or its name
    section: str
    ord: int
    text: str

    @property
    def header(self) -> str:
        return f"{self.kind}: {self.title}" + (f" § {self.section}" if self.section else "")


def files(lib) -> list[Path]:
    out = []
    for d in DIRS:
        p = lib.root / d
        if p.is_dir():
            out += sorted(f for f in p.rglob("*.md") if not f.name.startswith(("_", ".")))
    return out


def rel(lib, path: Path) -> str:
    return path.relative_to(lib.root).as_posix()


def kind_of(relpath: str) -> str:
    return DIRS.get(relpath.split("/", 1)[0], "note")


def about(lib, relpath: str) -> str:
    """The item a notes/<key>.md file is about ('' if none)."""
    parts = relpath.split("/")
    if parts[0] != "notes" or len(parts) != 2:
        return ""
    return lib.resolve(Path(parts[1]).stem) or ""


def level(lib, relpath: str, text: str | None = None) -> str:
    """The note's sharing level (see the module docstring)."""
    p = lib.root / relpath
    if text is None:
        try:
            text = p.read_text("utf-8", "replace")
        except FileNotFoundError:
            return "confidential"
    m = FRONT.match(text)
    if m:
        fm = re.search(r"^sharing:\s*['\"]?(\w+)", m.group(1), re.M)
        if fm:
            return fm.group(1) if fm.group(1) in S.LEVELS else "confidential"
    cited = {r.key for r in refs.find(text)} | set(TICKED.findall(text))
    for c in cited:
        k = lib.resolve(c)
        if k and lib.get(k).sharing == "confidential":
            return "confidential"
    parts = relpath.split("/")
    if parts[0] == "notes" and len(parts) == 2:
        k = lib.resolve(Path(parts[1]).stem)
        if not k:
            return "confidential"
        return "confidential" if lib.get(k).sharing == "confidential" else "private"
    lib.reload_config()
    return "private" if lib.config.get("notes", {}).get("to_models", False) else "confidential"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "s"


def _split(text: str) -> list[str]:
    """Paragraph-aligned pieces of at most MAX_CHARS (a longer paragraph is cut)."""
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        while len(para) > MAX_CHARS:
            out.append(para[:MAX_CHARS])
            para = para[MAX_CHARS:]
        if cur and len(cur) + len(para) + 2 > MAX_CHARS:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def passages(lib, path: Path, text: str | None = None) -> list[Passage]:
    r = rel(lib, path)
    text = path.read_text("utf-8", "replace") if text is None else text
    body = FRONT.sub("", text, count=1)
    heads = list(HEADING.finditer(body))
    title = heads[0].group(2) if heads and heads[0].start() == body.find("#") and \
        not body[:heads[0].start()].strip() else path.stem
    spans = []                      # (section title, text)
    pos, cur = 0, ""
    for h in heads:
        spans.append((cur, body[pos:h.start()]))
        cur, pos = h.group(2), h.end()
    spans.append((cur, body[pos:]))
    out, seen = [], set()
    ab, kind = about(lib, r), kind_of(r)
    for sec, t in spans:
        for piece in _split(t):
            h8 = hashlib.sha1(piece.encode()).hexdigest()[:8]
            pid = f"{r}#{_slug(sec) if sec else 'top'}#{h8}"
            if pid in seen:
                continue
            seen.add(pid)
            out.append(Passage(pid, r, kind, ab, title, sec, len(out), piece))
    return out
