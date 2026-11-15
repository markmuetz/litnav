"""Quiz and discussion logs: quizzes/<key>.md (concepts: quizzes/concept-<slug>.md).

One file per paper or concept, one dated section per session:

    ## 2026-11-16 (quiz)
    | # | question | reference answer | result |
    |---|---|---|---|

    ## 2026-11-20 (discussion)
    - Explained well: ...
    - Shaky: ...
    - Missing: ...
    - Corrections (with chunk IDs): ...

Read the log before the next session so it goes deeper instead of repeating.
Reference answers are pointers (a sentence and a section or figure), checked
against the paper; they are not a second set of notes.
"""
from __future__ import annotations

import datetime as dt
import re

from .util import atomic_write

KINDS = ("quiz", "discussion")
HEAD = re.compile(r"^## (\d{4}-\d\d-\d\d)(?:\s*\((.*)\))?\s*$", re.M)


def path(lib, ident: str):
    if ident.startswith("concept:"):
        return lib.root / "quizzes" / f"concept-{ident.split(':', 1)[1]}.md"
    return lib.root / "quizzes" / f"{lib.get(ident).key}.md"


def sessions(lib, ident: str) -> list[dict]:
    p = path(lib, ident)
    if not p.is_file():
        return []
    text = p.read_text("utf-8")
    heads = list(HEAD.finditer(text))
    return [{"date": m.group(1), "kind": m.group(2) or "", "body":
             text[m.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)].strip()}
            for i, m in enumerate(heads)]


def listing(lib) -> list[dict]:
    d = lib.root / "quizzes"
    out = []
    for p in sorted(d.glob("*.md")) if d.is_dir() else []:
        ident = "concept:" + p.stem[8:] if p.stem.startswith("concept-") else p.stem
        dates = HEAD.findall(p.read_text("utf-8"))
        out.append({"id": ident, "sessions": len(dates), "last": max((x[0] for x in dates), default="")})
    return out


def start(lib, ident: str, kind: str = "quiz", today: dt.date | None = None) -> str:
    """Append a dated section for a new session; returns the file path."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    p = path(lib, ident)
    text = p.read_text("utf-8") if p.is_file() else (
        f"# {ident}: quizzes and discussions\n\nQuestions on what the notes do *not* cover. "
        "Answers are pointers into the paper, checked against it on the day, not a second "
        "set of notes.\n")
    day = (today or dt.date.today()).isoformat()
    if kind == "quiz":
        body = "| # | question | reference answer | result |\n|---|---|---|---|\n"
    else:
        body = "- Explained well:\n- Shaky:\n- Missing:\n- Corrections (with chunk IDs):\n"
    atomic_write(p, text.rstrip("\n") + f"\n\n## {day} ({kind})\n\n{body}")
    return str(p)
