"""Section-aware chunks with stable IDs (design § 9, plan revisions).

- split by section; long sections at paragraph boundaries (long paragraphs
  at sentence boundaries); never across a top-level section boundary;
- short subsections merge with their neighbours inside the same top-level
  section;
- ~300 words, at most ~420, with ~15% overlap inside a section;
- the abstract and each figure/table caption are chunks of their own;
- reference lists are not chunked;
- papers with no detected structure fall back to fixed overlapping windows.

ID: `<key>#<section-slug>#<hash8>`, the hash over the chunk's normalised
text, so re-chunking keeps the IDs of unchanged text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import sections as S
from .util import short_hash

VERSION = 1
TARGET, MAX, MIN, OVERLAP = 300, 420, 60, 45
WINDOW, STRIDE = 300, 255


@dataclass
class Chunk:
    id: str
    key: str
    section: str          # slug
    section_title: str
    kind: str             # abstract, body, caption, front, appendix, ack, window
    ord: int
    start: int
    end: int
    text: str


_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
_PARA = re.compile(r"\n[ \t]*\n+")


def _words(s: str) -> int:
    return len(s.split())


def _paragraphs(text: str, base: int) -> list[tuple[int, int, str]]:
    out, pos = [], 0
    for m in list(_PARA.finditer(text)) + [None]:
        end = m.start() if m else len(text)
        seg = text[pos:end]
        if seg.strip():
            lead = len(seg) - len(seg.lstrip())
            s = seg.strip()
            out.append((base + pos + lead, base + pos + lead + len(s), s))
        pos = m.end() if m else len(text)
    # pdftotext often has no blank lines: fall back to sentence groups
    res = []
    for a, b, s in out:
        if _words(s) <= MAX:
            res.append((a, b, s))
            continue
        off = 0
        for sent in _SENT.split(s):
            i = s.find(sent, off)
            res.append((a + i, a + i + len(sent), sent))
            off = i + len(sent)
    return res


def _pack(paras, key, slug, title, kind, ord0):
    """Greedy packing of paragraphs into ~TARGET-word chunks. After each
    chunk, its last paragraph(s) are carried into the next as overlap, if they
    are short enough to be overlap rather than most of a chunk."""
    spans, cur, fresh = [], [], 0          # fresh: paragraphs not carried over

    def flush():
        nonlocal cur, fresh
        spans.append(list(cur))
        tail, w = [], 0
        for x in reversed(cur):
            tail.insert(0, x)
            w += _words(x[2])
            if w >= OVERLAP:
                break
        cur = tail if w <= TARGET // 2 and len(tail) < len(cur) else []
        fresh = 0

    for p in paras:
        if fresh and sum(_words(x[2]) for x in cur) + _words(p[2]) > MAX:
            flush()
        cur.append(p)
        fresh += 1
        if sum(_words(x[2]) for x in cur) >= TARGET:
            flush()
    if fresh:
        spans.append(cur)
    out = []
    for i, sp in enumerate(spans):
        text = " ".join(re.sub(r"\s+", " ", x[2]) for x in sp)
        out.append(Chunk("", key, slug, title, kind, ord0 + i, sp[0][0], sp[-1][1], text))
    return out


def windows(text: str, key: str) -> list[Chunk]:
    toks = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    out = []
    for i in range(0, max(1, len(toks) - WINDOW // 4), STRIDE):
        span = toks[i:i + WINDOW]
        if not span:
            break
        a, b = span[0][0], span[-1][1]
        out.append(Chunk("", key, "text", "", "window", len(out), a, b,
                         re.sub(r"\s+", " ", text[a:b])))
    return out


def chunk(key: str, parsed: S.Parsed) -> list[Chunk]:
    text = parsed.text
    caps = parsed.captions
    in_cap = lambda a, b: any(ca <= a and b <= cb for ca, cb in caps)
    out: list[Chunk] = []

    if not parsed.detected:
        # strip a trailing reference list if one was found, then fixed windows
        end = next((s.start for s in parsed.sections if s.kind == "refs"), len(text))
        out = windows(text[:end], key)
    else:
        # group sections by top-level parent; never pack across groups
        groups: list[list[S.Section]] = []
        for s in parsed.sections:
            if s.kind == "refs":
                continue
            if s.level == 1 or not groups or groups[-1][0].kind != s.kind:
                groups.append([s])
            else:
                groups[-1].append(s)
        ord_ = 0
        for g in groups:
            head = g[0]
            kind = head.kind
            paras = []
            for s in g:
                body = text[s.start:s.end]
                # drop the heading line itself
                nl = body.find("\n")
                skip = nl + 1 if 0 <= nl < 120 and s.slug != "front" else 0
                paras += [p for p in _paragraphs(body[skip:], s.start + skip) if not in_cap(p[0], p[1])]
            if not paras:
                continue
            # pack subsection by subsection so a chunk's label is right, merging
            # short subsections forward within the group
            by_sec, cur_sec, buf = [], None, []
            for p in paras:
                sec = next(s for s in reversed(g) if s.start <= p[0])
                if cur_sec is None:
                    cur_sec = sec
                if sec is not cur_sec and sum(_words(x[2]) for x in buf) >= MIN * 2:
                    by_sec.append((cur_sec, buf))
                    cur_sec, buf = sec, []
                buf.append(p)
            if buf:
                if by_sec and sum(_words(x[2]) for x in buf) < MIN * 2:
                    by_sec[-1] = (by_sec[-1][0], by_sec[-1][1] + buf)   # short tail: merge back
                else:
                    by_sec.append((cur_sec, buf))
            for sec, ps in by_sec:
                label = sec.title if sec is head else f"{head.title} / {sec.title}"
                ck = "abstract" if kind == "abstract" else kind
                new = _pack(ps, key, sec.slug, label, ck, ord_)
                ord_ += len(new)
                out += new
    # captions: their own chunks (only inside body text, not in references)
    refs_start = next((s.start for s in parsed.sections if s.kind == "refs"), len(text))
    for ca, cb in caps:
        if ca >= refs_start:
            continue
        t = re.sub(r"\s+", " ", text[ca:cb]).strip()
        if _words(t) >= 6:
            out.append(Chunk("", key, "caption", t.split(".")[0][:40], "caption", len(out), ca, cb, t))
    seen = set()
    final = []
    for c in out:
        if _words(c.text) < 8:
            continue
        h = short_hash(" ".join(c.text.lower().split()))
        cid = f"{key}#{c.section}#{h}"
        if cid in seen:
            continue
        seen.add(cid)
        c.id = cid
        final.append(c)
    return final


def header(author: str, year: str, title: str, section_title: str) -> str:
    who = f"{author} et al." if author else ""
    bits = [b for b in (f"{who} {year}".strip(), title, section_title) if b]
    return " — ".join(bits)
