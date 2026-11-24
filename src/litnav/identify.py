"""Work out what a PDF is, and verify it (design § 7).

Ladder, cheapest and most certain first:
  1. a DOI in the first pages (or an AMS SICI DOI in the filename) -> Crossref
  2. an arXiv id in the text or filename -> arXiv; the key gets _preprint
  3. a bibliographic title query against Crossref with the opening lines
  (OCR first if there is no text layer.)

Every proposal is verified: the resolved title's distinctive words must
appear near the front of the document. This is what catches a *cited* DOI
being mistaken for the document's own. A title query is a guess (Crossref
always returns its best match, related or not), so it must clear a higher
bar than an identifier.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import extract, keys
from .sources import arxiv, crossref

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:<>A-Za-z0-9]+")
AMS_SICI = re.compile(r"^[a-z]+-(\d{4}-\d{3}[\dx])_(\d{4})_(\d{3})_(\d{4})_([a-z0-9]+)_(\d)_(\d)_co_(\d)$", re.I)
MARKER_HINTS = {"supporting": "supporting", "supplement": "supplement", "preprint": "preprint",
                "pre_print": "preprint", "poster": "poster", "slides": "slides",
                "manuscript": "manuscript", "corrigendum": "corrigendum", "erratum": "corrigendum"}


@dataclass
class Proposal:
    file: str
    status: str = ""             # ok, low-confidence, no-match, no-key
    key: str = ""
    via: str = ""
    score: float = 0.0
    title: str = ""
    doi: str = ""
    arxiv: str = ""
    bibtex: str = ""
    year: int | None = None
    licence: str = ""            # URL of an in-force Creative Commons licence (Crossref)
    extra: dict = field(default_factory=dict)


def clean_doi(d: str) -> str:
    d = d.rstrip(".,;)]}>")
    # a DOI followed by text glued on by pdftotext: cut at known tails
    return re.split(r"(?i)(?:received|accepted|published|copyright)", d)[0].rstrip(".,;")


def ams_doi(stem: str) -> str | None:
    m = AMS_SICI.match(stem)
    if not m:
        return None
    issn, yr, vol, page, code, a, b, c = m.groups()
    return f"10.1175/{issn.upper()}({yr}){vol}<{page}:{code.upper()}>{a}.{b}.CO;{c}"


def marker_for(name: str) -> str | None:
    low = name.lower().replace("-", "_")
    for hint, m in MARKER_HINTS.items():
        if hint in low or hint.replace("_", "") in low.replace("_", ""):
            return m
    return None


def verify(title: str, text: str) -> float:
    """Share of the title's distinctive words found in the document's front."""
    tw = [w for w in keys.words(title) if w not in keys.STOP and len(w) > 3]
    if len(tw) < 3:
        # short titles: require every word, exactly, in the opening text
        tw = [w for w in keys.words(title) if w not in keys.STOP]
        if not tw:
            return 0.0
    hay = set(keys.words(text[:8000]))
    return sum(1 for w in tw if w in hay) / len(tw)


def identify(pdf: Path, client, exists, *, cfg: dict, doi: str | None = None,
             text: str | None = None) -> Proposal:
    p = Proposal(file=pdf.name)
    via = ""
    if text is None:
        text, src = extract.head(pdf, pages=5)
        via = "ocr+" if src == "ocr" else ""
    imp = cfg.get("import", {})
    bar_id = imp.get("verify_identifier", 0.6)
    bar_title = imp.get("verify_title_query", 0.75)
    extra_stop = cfg.get("keys", {}).get("extra_stop_words", [])
    marker = marker_for(pdf.stem)

    msg, how, rec = None, "", None
    if doi:
        msg, how = crossref.work(client, doi), "doi(given)"
    if msg is None:
        seen = set()
        for m in DOI_RE.finditer(text):
            d = clean_doi(m.group(0))
            if d.lower() in seen:
                continue
            seen.add(d.lower())
            cand = crossref.work(client, d)
            if cand and verify(crossref.title(cand), text) >= bar_id:
                msg, how = cand, "doi"
                break
            if len(seen) >= 3:          # the document's own DOI is near the top
                break
    if msg is None and ams_doi(pdf.stem):
        msg, how = crossref.work(client, ams_doi(pdf.stem)), "amsname"
    aid = None
    if msg is None:
        aid = arxiv.find_id(text, pdf.stem)
        if aid:
            rec = arxiv.lookup(client, aid)
            if rec:
                how = "arxiv"
                marker = marker or "preprint"
    if msg is None and rec is None:
        lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 25][:4]
        if lines:
            hits = crossref.query(client, " ".join(lines))
            if hits:
                msg, how = hits[0], "title"

    p.via = via + how
    if msg is None and rec is None:
        p.status = "no-match"
        p.arxiv = aid or ""      # found but the arXiv API did not answer: say so
        return p
    if rec is not None:
        p.title, p.year, p.arxiv = rec["title"], rec["year"], rec["id"]
        p.doi = rec.get("doi", "")
        fam = arxiv.family(rec["authors"][0])
        p.extra["author"] = f"{fam}, x"
        p.key = keys.derive_unique(fam, rec["year"], rec["title"], exists, marker=marker,
                                   extra_stop=extra_stop) or ""
        p.bibtex = arxiv.to_bibtex(rec, p.key) if p.key else ""
    else:
        p.title, p.year = crossref.title(msg), crossref.issue_year(msg)
        p.doi = (msg.get("DOI") or "").lower()
        p.extra["author"] = f"{crossref.first_family(msg)}, x"
        p.key = crossref.key_for(msg, exists, marker=marker, extra_stop=extra_stop) or ""
        p.bibtex = crossref.to_bibtex(msg, p.key) if p.key else ""
        from .licences import best
        lic = best(msg)
        p.licence = lic[1] if lic else ""
    if not p.key:
        p.status = "no-key"
        return p
    p.score = verify(p.title, text)
    bar = bar_title if how == "title" else bar_id
    p.status = "ok" if p.score >= bar else "low-confidence"
    return p
