"""Discovery: papers the library does not hold yet.

Sources
  candidates   outside works cited by several library papers (graph.json,
               built by `litnav graph` from OpenAlex reference lists)
  explore      Semantic Scholar recommendations for seed papers (metadata
               only: the seeds' DOIs are what is sent)

The to-fetch list is `discovery/to-fetch.tsv`, one row per paper, keyed on
the lower-cased DOI:

    doi  title  first_author  year  source  added  status  note

`source` says where it came from (`candidates:7`, `explore:<seed>`,
`note:<key>`); `status` is blank, `opened` or `skip`. Tools append and
dedupe; they never rewrite a row the user wrote. `skip` rows stay: they stop
the same paper being proposed again. A row whose DOI the library now holds
is dropped by `prune`, which `import --apply` runs.

PDFs are never downloaded: `to-fetch open` opens DOIs in the browser.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import urllib.parse

from . import keys as K
from . import sharing as S
from .net import NetError, Refused
from .util import atomic_write

COLUMNS = ["doi", "title", "first_author", "year", "source", "added", "status", "note"]
S2_REC = "https://api.semanticscholar.org/recommendations/v1/papers/forpaper/"


def norm_doi(d: str) -> str:
    d = (d or "").strip().lower()
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", d)
    return d


def fold_title(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", K.fold_text(t or "")).split())


# ---- what the library holds

class Holdings:
    def __init__(self, lib):
        self.dois, self.titles, self.prefixes, self.key_titles = set(), {}, {}, {}
        for it in lib.items():
            if it.doi:
                self.dois.add(norm_doi(it.doi))
            if it.title:
                self.titles[fold_title(it.title)] = it.key
                self.key_titles[it.key] = fold_title(it.title)
            m = re.match(r"^([a-z]+)(\d{4})", it.key)
            if m:
                self.prefixes.setdefault(m.group(0), []).append(it.key)

    def status(self, doi: str, title: str, first_author: str = "", year=None) -> tuple[str, str]:
        """('held', key) / ('maybe', key) / ('', '')."""
        if doi and norm_doi(doi) in self.dois:
            return "held", ""
        ft = fold_title(title)
        if ft and ft in self.titles:
            return "held", self.titles[ft]
        if ft:
            words = set(ft.split())
            for t, k in self.titles.items():
                tw = set(t.split())
                if len(words) >= 4 and len(words & tw) / max(len(words | tw), 1) >= 0.8:
                    return "maybe", k
        sur = K.surname(first_author) if first_author else ""
        if sur and year:
            pre = f"{K.fold(sur).lower()}{year}"
            pre = re.sub(r"[^a-z0-9]", "", pre)
            words = set(ft.split()) - {"the", "a", "an", "of", "and", "in", "on", "for", "to", "with"}
            for k in self.prefixes.get(pre, []):
                tw = set(self.key_titles.get(k, "").split())
                if tw and (not words or len(words & tw) / len(words) >= 0.4):
                    return "maybe", k
                if not tw and _key_word_in(k, pre, ft):
                    return "maybe", k           # an item with no bib: only its key to go on
        return "", ""


def _key_word_in(key: str, prefix: str, folded_title: str) -> bool:
    """Does a key's word (dee2011erainterim -> 'erainterim') appear in a title,
    spaces ignored? For items with no bib entry to match on."""
    word = re.sub(r"_.*$", "", key[len(prefix):])
    word = re.sub(r"\d+$", "", word)
    return len(word) >= 4 and word in folded_title.replace(" ", "")


# ---- the to-fetch list

def tofetch_path(lib):
    return lib.root / "discovery" / "to-fetch.tsv"


def load(lib) -> list[dict]:
    p = tofetch_path(lib)
    if not p.is_file():
        return []
    rows = list(csv.DictReader(io.StringIO(p.read_text("utf-8")), delimiter="\t"))
    return [{c: (r.get(c) or "") for c in COLUMNS} for r in rows]


def save(lib, rows) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, COLUMNS, delimiter="\t", lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: str(r.get(c, "") or "").replace("\t", " ").replace("\n", " ") for c in COLUMNS})
    atomic_write(tofetch_path(lib), buf.getvalue())


def add(lib, new: list[dict]) -> int:
    """Append rows whose DOI (or, without one, title) is not listed yet."""
    rows = load(lib)
    seen = {norm_doi(r["doi"]) for r in rows if r["doi"]} | \
           {fold_title(r["title"]) for r in rows if not r["doi"]}
    n = 0
    for r in new:
        ident = norm_doi(r.get("doi", "")) or fold_title(r.get("title", ""))
        if not ident or ident in seen:
            continue
        seen.add(ident)
        rows.append({**{c: "" for c in COLUMNS}, **r, "doi": norm_doi(r.get("doi", "")),
                     "added": r.get("added") or dt.date.today().isoformat()})
        n += 1
    if n:
        save(lib, rows)
    return n


def set_status(lib, dois, status: str) -> int:
    rows, want, n = load(lib), {norm_doi(d) for d in dois}, 0
    for r in rows:
        if norm_doi(r["doi"]) in want:
            r["status"] = status
            n += 1
    if n:
        save(lib, rows)
    return n


def prune(lib) -> list[dict]:
    """Drop rows the library now holds (by DOI). Returns the dropped rows."""
    rows = load(lib)
    if not rows:
        return []
    h = Holdings(lib)
    keep = [r for r in rows if not (r["doi"] and norm_doi(r["doi"]) in h.dois)]
    gone = [r for r in rows if r not in keep]
    if gone:
        save(lib, keep)
    return gone


def listed(lib) -> dict[str, str]:
    """DOI or folded title -> status, for everything on the list."""
    return {(norm_doi(r["doi"]) or fold_title(r["title"])): (r["status"] or "listed") for r in load(lib)}


# ---- candidates from the citation graph

def candidates(lib, *, min_cites: int = 3, show_skipped: bool = False) -> list[dict]:
    p = lib.index_dir / "graph.json"
    if not p.is_file():
        raise ValueError("no citation graph yet: run `litnav graph` first")
    g = json.loads(p.read_text())
    h, lst = Holdings(lib), listed(lib)
    out = []
    for c in g.get("candidates", []):
        if c.get("cited_by_library", 0) < min_cites:
            continue
        held, like = h.status(c.get("doi", ""), c.get("title", ""), c.get("first_author", ""), c.get("year"))
        if held == "held":
            continue
        st = lst.get(norm_doi(c.get("doi", "")) or fold_title(c.get("title", "")), "")
        if st == "skip" and not show_skipped:
            continue
        out.append({**c, "maybe_held": like if held == "maybe" else "", "listed": st})
    return out


# ---- explore: Semantic Scholar recommendations

def explore(lib, client, seeds: list[str], *, k: int = 20, log=print) -> list[dict]:
    """Recommendations for each seed paper, merged: papers recommended for
    several seeds rank first. Only the seeds' DOIs leave the machine."""
    h, lst = Holdings(lib), listed(lib)
    headers = {"x-api-key": os.environ["S2_API_KEY"]} if os.environ.get("S2_API_KEY") else None
    merged: dict[str, dict] = {}
    for key in seeds:
        if not lib.get(key).allowed(S.LOOKUP):
            raise Refused(f"{lib.get(key).key} is {lib.get(key).sharing}: never looked up")
    for key in seeds:
        it = lib.get(key)
        if not it.doi:
            log(f"{it.key}: no DOI; skipped")
            continue
        url = S2_REC + "DOI:" + urllib.parse.quote(it.doi, safe="/")
        try:
            d = client.get_json(url, purpose=S.LOOKUP, items=[it], cache=False, headers=headers,
                                params={"fields": "title,year,authors,externalIds,citationCount",
                                        "limit": min(k * 2, 100)})
        except NetError as e:    # includes a 404 for papers S2 doesn't know
            log(f"{it.key}: no recommendations ({e})")
            continue
        for rank, p in enumerate(d.get("recommendedPapers", [])):
            doi = norm_doi((p.get("externalIds") or {}).get("DOI", ""))
            ident = doi or fold_title(p.get("title", ""))
            if not ident:
                continue
            au = (p.get("authors") or [{}])[0].get("name", "")
            held, like = h.status(doi, p.get("title", ""), au, p.get("year"))
            if held == "held" or lst.get(ident) == "skip":
                continue
            m = merged.setdefault(ident, {"doi": doi, "title": p.get("title", ""), "year": p.get("year"),
                                          "first_author": au, "cited_by_count": p.get("citationCount"),
                                          "seeds": [], "score": 0.0,
                                          "maybe_held": like if held == "maybe" else "",
                                          "listed": lst.get(ident, "")})
            m["seeds"].append(it.key)
            m["score"] += 1 / (rank + 10)
    return sorted(merged.values(), key=lambda m: -m["score"])[:k]
