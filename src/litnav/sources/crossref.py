"""Crossref: works by DOI, bibliographic queries, BibTeX built from the JSON.

We build BibTeX ourselves rather than asking Crossref for it, so the year is
the *issue* year (published-print), not the online-first date.
"""
from __future__ import annotations

import html
import re
import urllib.parse

from .. import keys
from ..net import LOOKUP, NetError

API = "https://api.crossref.org/works"

TYPE_MAP = {"journal-article": "article", "proceedings-article": "inproceedings",
            "book-chapter": "incollection", "book": "book", "monograph": "book",
            "edited-book": "book", "report": "techreport", "posted-content": "misc",
            "dissertation": "phdthesis", "reference-entry": "incollection"}


def work(client, doi: str, items=()) -> dict | None:
    try:
        d = client.get_json(f"{API}/{urllib.parse.quote(doi, safe='/')}", purpose=LOOKUP, items=items)
    except NetError:
        return None
    return d.get("message")


def query(client, text: str, rows: int = 3) -> list[dict]:
    try:
        d = client.get_json(API, purpose=LOOKUP, params={
            "query.bibliographic": text[:250], "rows": rows,
            "select": "DOI,author,title,issued,published-print,published-online,"
                      "container-title,type,volume,issue,page,publisher,subtype,license"})
    except NetError:
        return []
    return d.get("message", {}).get("items", [])


def issue_year(msg: dict) -> int | None:
    """Prefer the issue date over online-first: a recurring trap."""
    for f in ("published-print", "issued", "published", "published-online"):
        parts = (msg.get(f) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def title(msg: dict) -> str:
    t = (msg.get("title") or [""])[0]
    return clean_title(t)


def clean_title(t: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", "", t))       # <i>, <sub> from JATS
    return re.sub(r"\s+", " ", t).strip()


def first_family(msg: dict) -> str:
    for a in msg.get("author") or []:
        if a.get("family"):
            return a["family"]
        if a.get("name"):
            return a["name"]
    return ""


_TEX = {"&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_", "$": r"\$"}


def tex(s: str) -> str:
    return re.sub(r"(?<!\\)[&%#_$]", lambda m: _TEX[m.group(0)], s)


def to_bibtex(msg: dict, key: str) -> str:
    etype = TYPE_MAP.get(msg.get("type", ""), "misc")
    f = []
    f.append(("title", tex(title(msg))))
    names = []
    for a in msg.get("author") or []:
        if a.get("family"):
            names.append(f"{a['family']}, {a.get('given', '')}".rstrip(", "))
        elif a.get("name"):
            names.append("{" + a["name"] + "}")
    if names:
        f.append(("author", tex(" and ".join(names))))
    ct = (msg.get("container-title") or [""])[0]
    if ct:
        f.append(("journal" if etype == "article" else "booktitle", tex(clean_title(ct))))
    for src, dst in (("volume", "volume"), ("issue", "number")):
        if msg.get(src):
            f.append((dst, str(msg[src])))
    if msg.get("page"):
        f.append(("pages", re.sub(r"\s*[-–—]+\s*", "--", msg["page"])))
    y = issue_year(msg)
    if y:
        f.append(("year", str(y)))
    if msg.get("publisher") and etype != "article":
        f.append(("publisher", tex(msg["publisher"])))
    if msg.get("DOI"):
        f.append(("doi", msg["DOI"].lower()))
    body = ",\n".join(f"    {n} = {{{v}}}" for n, v in f)
    return f"@{etype}{{{key},\n{body}\n}}\n"


def key_for(msg: dict, exists, marker=None, extra_stop=()) -> str | None:
    return keys.derive_unique(first_family(msg), issue_year(msg), title(msg), exists,
                              marker=marker, extra_stop=extra_stop)
