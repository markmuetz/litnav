"""Find section structure in plain extracted text (design § 9).

Heuristics, in order of trust:

1. Manual headings: when the item's meta.toml has `[files] sections =
   "manual"`, lines in text.txt starting with `# ` or `## ` are the headings,
   used exclusively (the user has fixed this paper by hand). Opt-in, because
   some papers' text naturally contains such lines.
2. Numbered headings (`1. Introduction`, `2 DATA AND METHODS`, `3.2 Results`,
   `IV. DISCUSSION`), accepted only as a plausible sequence: top-level numbers
   must start at 1 or 2 and go up by one; subsections must belong to the
   current top-level section. This rejects table rows, list items, `3 km`.
3. Known unnumbered headings (Abstract, Introduction, ..., References),
   alone on a line.

Also: page headers/footers that repeat on many pages are dropped; figure and
table captions are pulled out as their own units.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .extract import normalise_text

VERSION = 2       # 2: headings wrapped onto a second line are joined

KNOWN = {
    "abstract": "abstract", "summary": "abstract", "key points": "front",
    "plain language summary": "front", "significance statement": "front",
    "introduction": "body", "background": "body", "data": "body", "methods": "body",
    "method": "body", "methodology": "body", "data and methods": "body",
    "materials and methods": "body", "model": "body", "results": "body",
    "discussion": "body", "results and discussion": "body", "conclusion": "body",
    "conclusions": "body", "summary and conclusions": "body", "discussion and conclusions": "body",
    "concluding remarks": "body", "summary and discussion": "body",
    "acknowledgments": "ack", "acknowledgements": "ack", "acknowledgment": "ack",
    "acknowledgement": "ack", "data availability": "ack", "data availability statement": "ack",
    "code availability": "ack", "author contributions": "ack", "competing interests": "ack",
    "open research": "ack", "funding": "ack",
    "references": "refs", "bibliography": "refs", "literature cited": "refs",
    "references cited": "refs",
}
NUM = re.compile(r"^(?P<num>\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(?P<title>[A-Z][^\n]{1,90})$")
ROMAN = re.compile(r"^(?P<num>I{1,3}|IV|V|VI{1,3}|IX|X)\.\s+(?P<title>[A-Z][^\n]{1,80})$")
APPX = re.compile(r"^(?P<title>APPENDIX(?:\s+[A-Z0-9]{1,2})?|Appendix(?:\s+[A-Z0-9]{1,2})?)\b[.:]?(?:\s+[^\n]{0,80})?$")
CAPTION = re.compile(r"^(?:FIG(?:URE)?\.?|Fig(?:ure)?\.?|TABLE|Table)\s*S?\d+[.:|]?\s", re.M)
ROMANS = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
MANUAL = re.compile(r"^(#{1,3})\s+(.+)$")


@dataclass
class Section:
    slug: str
    title: str
    level: int
    kind: str            # front, abstract, body, ack, refs, appendix
    start: int
    end: int = 0
    number: str = ""


@dataclass
class Parsed:
    text: str                                  # normalised text the offsets refer to
    sections: list[Section] = field(default_factory=list)
    captions: list[tuple[int, int]] = field(default_factory=list)
    detected: bool = False
    method: str = "none"


def strip_running_heads(text: str) -> str:
    """Drop lines that recur at the top or bottom of many pages."""
    pages = text.split("\f")
    if len(pages) < 4:
        return text
    edge = Counter()
    for p in pages:
        lines = [l.strip() for l in p.strip("\n").splitlines() if l.strip()]
        for l in set(lines[:3] + lines[-3:]):
            edge[_page_norm(l)] += 1
    rep = {l for l, n in edge.items() if n >= max(3, len(pages) * 0.3) and len(l) < 120}
    if not rep:
        return text
    out = []
    for p in pages:
        out.append("\n".join(l for l in p.splitlines() if _page_norm(l.strip()) not in rep))
    return "\f".join(out)


def _page_norm(l: str) -> str:
    return re.sub(r"\d+", "#", l.lower())


# A heading that ends on one of these has wrapped onto the next line.
TRAILING = {"the", "of", "and", "in", "on", "to", "for", "with", "a", "an", "from", "by", "at",
            "between", "over", "under", "into", "its", "their", "as", "or", "versus", "vs"}


def _wrapped(title: str, lines: list[str], i: int) -> str | None:
    """The continuation of a heading cut at line i, or None."""
    w = title.split()
    if not w or w[-1].lower() not in TRAILING or i + 1 >= len(lines):
        return None
    nxt = lines[i + 1].strip()
    if not nxt or len(nxt) > 70 or NUM.match(nxt) or not _plausible_title(nxt):
        return None
    if nxt.endswith(".") and not nxt.endswith(("?", "!")):
        return None
    return nxt


def _clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip().rstrip(".:")
    return t.title() if t.isupper() and len(t) > 3 else t


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "section"


def _plausible_title(t: str) -> bool:
    t = t.strip()
    if len(t) < 3 or len(t.split()) > 14:
        return False
    if t.endswith((",", ";")) or re.search(r"\b(et al|Fig|Eq|Table)\b", t):
        return False
    if re.search(r"\d{3,}|[=<>±×]", t):            # numbers and maths: a table row or equation
        return False
    letters = sum(c.isalpha() for c in t)
    return letters >= 0.6 * len(t.replace(" ", ""))


def _known_kind(line: str) -> str | None:
    t = re.sub(r"[^a-z ]", "", line.lower()).strip()
    t = re.sub(r"\s+", " ", t)
    return KNOWN.get(t)


def parse(raw: str, manual: bool = False) -> Parsed:
    text = normalise_text(strip_running_heads(raw)).replace("\f", "\n")
    lines = text.splitlines(keepends=True)
    offs, pos = [], 0
    for l in lines:
        offs.append(pos)
        pos += len(l)

    marks = [(i, MANUAL.match(l.strip())) for i, l in enumerate(lines)] if manual else []
    marks = [(i, m) for i, m in marks if m]
    if marks:
        secs = []
        for i, m in marks:
            title = m.group(2).strip()
            kind = _known_kind(title) or "body"
            secs.append(Section(_slug(title), title, len(m.group(1)), kind, offs[i]))
        return _finish(Parsed(text, secs, method="manual"))

    cands = []      # (line index, Section)
    top, sub_ok = 0, set()
    for i, l in enumerate(lines):
        s = l.strip()
        if not s or len(s) > 100:
            continue
        prev_blank = i == 0 or not lines[i - 1].strip() or lines[i - 1].strip().endswith(".")
        m = NUM.match(s) or ROMAN.match(s)
        if m and prev_blank and _plausible_title(m.group("title")):
            num = m.group("num")
            parts = [ROMANS[num]] if num in ROMANS else [int(x) for x in num.split(".")]
            raw_title = m.group("title")
            cont = _wrapped(raw_title, lines, i)
            title = _clean_title(raw_title + (" " + cont if cont else ""))
            if len(parts) == 1:
                n = parts[0]
                if (top == 0 and n in (1, 2)) or n == top + 1:
                    top = n
                    sub_ok = {(n, 1)}
                    kind = _known_kind(title) or "body"
                    cands.append((i, Section(_slug(num), title, 1, kind, offs[i], number=num)))
                    continue
            elif parts[0] == top and top > 0:
                if tuple(parts[:2]) in sub_ok or (len(parts) > 2):
                    sub_ok.add((parts[0], parts[1] + 1))
                    sub_ok.add(tuple(parts[:2]))
                    cands.append((i, Section(_slug(num), title, len(parts), "body", offs[i],
                                             number=".".join(map(str, parts)))))
                    continue
        if prev_blank or i == 0:
            kind = _known_kind(s) if len(s) < 50 else None
            if kind:
                cands.append((i, Section(_slug(s), _clean_title(s), 1, kind, offs[i])))
                continue
            m = APPX.match(s)
            if m and len(s) < 90:
                cands.append((i, Section(_slug(m.group("title")), _clean_title(s), 1, "appendix", offs[i])))

    numbered = [c for c in cands if c[1].number]
    # numbered headings are only trusted as a set of at least two top-level ones
    if len([c for c in numbered if c[1].level == 1]) < 2:
        cands = [c for c in cands if not c[1].number]
    # one References heading: the last one (earlier hits are usually a contents list)
    refs = [c for c in cands if c[1].kind == "refs"]
    if len(refs) > 1:
        cands = [c for c in cands if c[1].kind != "refs" or c is refs[-1]]
    secs = [c[1] for c in cands]
    detected = sum(1 for s in secs if s.kind == "body") >= 2
    p = Parsed(text, secs, detected=detected,
               method="numbered" if numbered and detected else ("known" if detected else "none"))
    return _finish(p)


def _finish(p: Parsed) -> Parsed:
    secs = sorted(p.sections, key=lambda s: s.start)
    front_end = secs[0].start if secs else len(p.text)
    out = []
    if front_end > 0:
        out.append(Section("front", "Front matter", 1, "front", 0))
    out.extend(secs)
    seen = Counter()
    after_refs = False
    for i, s in enumerate(out):
        s.end = out[i + 1].start if i + 1 < len(out) else len(p.text)
        seen[s.slug] += 1
        if seen[s.slug] > 1:
            s.slug = f"{s.slug}-{seen[s.slug]}"
        # after the references, only appendices and acknowledgements are not references
        if s.kind == "refs":
            after_refs = True
        elif after_refs and s.kind == "body" and not s.number:
            s.kind = "refs"
    p.sections = [s for s in out if s.end > s.start]
    p.captions = [(m.start(), _para_end(p.text, m.start())) for m in CAPTION.finditer(p.text)]
    if not p.detected and p.method != "manual":
        p.detected = any(s.kind == "refs" for s in p.sections) and len(p.sections) >= 3
    if p.method == "manual":
        p.detected = True
    return p


_BLANK = re.compile(r"\n[ \t]*\n")


def _para_end(text: str, start: int) -> int:
    m = _BLANK.search(text, start, min(len(text), start + 1500))
    return m.start() if m else min(len(text), start + 1500)
