"""arXiv Atom API."""
from __future__ import annotations

import re

from ..net import LOOKUP, NetError

API = "https://export.arxiv.org/api/query"
NEW_ID = re.compile(r"(?:arxiv[:\s]*)?\b(\d{4}\.\d{4,5})(?:v\d+)?\b", re.I)
OLD_ID = re.compile(r"arxiv[:\s]*([a-z-]+(?:\.[A-Z]{2})?/\d{7})", re.I)
PARTICLES = {"de", "del", "della", "der", "den", "di", "da", "du", "van", "von",
             "le", "la", "dos", "das"}


def find_id(text: str, filename: str = "") -> str | None:
    m = OLD_ID.search(text) or re.search(r"arxiv[:\s]*(\d{4}\.\d{4,5})", text, re.I)
    if m:
        return m.group(1)
    m = NEW_ID.fullmatch(filename) or re.match(r"(\d{4}\.\d{4,5})(v\d+)?$", filename)
    return m.group(1) if m else None


def family(name: str) -> str:
    parts = name.split()
    i = next((j for j, w in enumerate(parts[1:-1], 1) if w.lower() in PARTICLES), len(parts) - 1)
    return " ".join(parts[i:])


def lookup(client, aid: str) -> dict | None:
    try:
        x = client.get(API, purpose=LOOKUP, params={"id_list": aid, "max_results": 1},
                       accept="application/atom+xml")
    except NetError:
        return None
    ent = x.split("<entry>", 1)[-1] if "<entry>" in x else ""

    def grab(tag, s=ent):
        m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", s, re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    title, pub = grab("title"), grab("published")
    names = [re.sub(r"\s+", " ", n).strip() for n in re.findall(r"<name>(.*?)</name>", ent, re.S)]
    if not (title and names and pub):
        return None
    doi = grab("arxiv:doi")
    return {"id": aid, "title": title, "authors": names, "year": int(pub[:4]), "doi": doi,
            "abstract": grab("summary")}


OAI = "https://oaipmh.arxiv.org/oai"


def licence(client, aid: str) -> str | None:
    """The licence URL arXiv records for a preprint ('' if none is given; None if
    the lookup failed). arXiv's default (nonexclusive-distrib) is not a CC licence."""
    try:
        x = client.get(OAI, purpose=LOOKUP, accept="text/xml",
                       params={"verb": "GetRecord", "identifier": f"oai:arXiv.org:{aid}", "metadataPrefix": "arXiv"})
    except NetError:
        return None
    if "<GetRecord>" not in x:
        return None
    m = re.search(r"<license>\s*(.*?)\s*</license>", x, re.S)
    return m.group(1) if m else ""


def to_bibtex(rec: dict, key: str) -> str:
    names = " and ".join(f"{family(n)}, {n[:len(n) - len(family(n))].strip()}".rstrip(", ")
                         for n in rec["authors"])
    f = [("title", rec["title"]), ("author", names), ("year", str(rec["year"])),
         ("eprint", rec["id"]), ("archiveprefix", "arXiv"),
         ("url", f"https://arxiv.org/abs/{rec['id']}")]
    body = ",\n".join(f"    {n} = {{{v}}}" for n, v in f)
    return f"@misc{{{key},\n{body}\n}}\n"
