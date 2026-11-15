"""References to papers in Markdown: Pandoc citation syntax.

    [@smith2021cold]   [@smith2021cold, sec. 4.2]   [@a2020x; @b2021y]   @smith2021cold

Code spans and fenced code blocks are skipped; an `@` preceded by a word
character (an email address) is not a citation. Keys match exactly: `@stein2014three`
is not a citation of `stein2014threedimensional`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CITE = re.compile(r"(?<![\w@.\\])-?@(?P<key>[A-Za-z0-9_]+(?:[:.#$%&+?<>~/-][A-Za-z0-9_]+)*)")
FENCE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.M | re.S)
INLINE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)
NOTE_DIRS = ("notes", "concepts", "topics", "projects", "reading", "derived", "quizzes")


@dataclass
class Ref:
    key: str
    start: int
    end: int
    line: int


def _mask(text: str) -> str:
    """Replace code with spaces, keeping offsets."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    text = FENCE.sub(blank, text)
    return INLINE.sub(blank, text)


def find(text: str) -> list[Ref]:
    masked = _mask(text)
    out = []
    for m in CITE.finditer(masked):
        k = m.group("key")
        out.append(Ref(k, m.start("key"), m.end("key"), masked.count("\n", 0, m.start()) + 1))
    return out


def md_files(lib) -> list[Path]:
    out = []
    for d in NOTE_DIRS:
        p = lib.root / d
        if p.is_dir():
            out += sorted(p.rglob("*.md"))
    return out


def check(lib) -> list[tuple[Path, Ref]]:
    """References that resolve to no item (by key, case, or alias)."""
    bad = []
    for f in md_files(lib):
        for r in find(f.read_text("utf-8", "replace")):
            if lib.resolve(r.key) is None:
                bad.append((f, r))
    return bad


def backlinks(lib) -> dict[str, list[str]]:
    """key -> files (relative to the library) that cite it."""
    out: dict[str, set] = {}
    for f in md_files(lib):
        for r in find(f.read_text("utf-8", "replace")):
            k = lib.resolve(r.key)
            if k:
                out.setdefault(k, set()).add(f.relative_to(lib.root).as_posix())
    return {k: sorted(v) for k, v in out.items()}


def rewrite(text: str, old: str, new: str) -> tuple[str, int]:
    """Replace citations of `old` (exactly, case-insensitively) with `new`."""
    refs = [r for r in find(text) if r.key.casefold() == old.casefold()]
    for r in reversed(refs):
        text = text[:r.start] + new + text[r.end:]
    return text, len(refs)


_BACKTICK = re.compile(r"(?<!`)`([A-Za-z0-9_]+)`(?!`)")


def convert_backticks(text: str, resolve) -> tuple[str, int]:
    """`key` -> [@key] where the backticked text is a real key (or alias,
    rewritten to the canonical key). Code blocks are left alone."""
    fenced = [(m.start(), m.end()) for m in FENCE.finditer(text)]
    in_fence = lambda i: any(a <= i < b for a, b in fenced)
    n = 0

    def sub(m):
        nonlocal n
        if in_fence(m.start()):
            return m.group(0)
        k = resolve(m.group(1))
        if not k:
            return m.group(0)
        n += 1
        return f"[@{k}]"
    return _BACKTICK.sub(sub, text), n
