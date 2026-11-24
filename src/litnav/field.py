"""The field: works around the library, with their public abstracts.

A thin local layer over OpenAlex, in <index>/field/field.sqlite (not tracked:
everything in it can be fetched again). It holds the works the library leans
on or that lean on it, their abstracts (public metadata: they may go to a
model), OpenAlex's topic for each, and the citation links among them and the
library. The owner's decisions about field works (want, skip, a note) are the
one part that cannot be rebuilt: they live in discovery/field.tsv, tracked.

Sets
  backward   works cited by at least `min_cites` library papers (from the
             citation graph, `litnav graph`)
  forward    works that cite many library papers (to come)

Kinds: research, review, dataset, model (a model description), project (an
intercomparison or campaign overview). Datasets and models are listed apart.
Nothing about confidential items is ever sent: only OpenAlex ids of works
already resolved by `litnav graph` (which skips them) are looked up.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import re
import sqlite3
from collections import Counter

from . import discovery as D
from .abstracts import MAX_LEN, clean, from_inverted
from .net import LOOKUP, NetError
from .sources import openalex

SELECT = ("id,doi,title,publication_year,authorships,type,primary_location,abstract_inverted_index,"
          "cited_by_count,citation_normalized_percentile,primary_topic,referenced_works")
SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
  id TEXT PRIMARY KEY, doi TEXT, title TEXT, year INTEGER, first_author TEXT, venue TEXT,
  type TEXT, kind TEXT, abstract TEXT, cited_by_count INTEGER, percentile REAL,
  topic TEXT, subfield TEXT, field TEXT, sets TEXT, library_citers INTEGER,
  cites_library INTEGER, score REAL, maybe_held TEXT, fetched TEXT);
CREATE TABLE IF NOT EXISTS links (src TEXT, dst TEXT, PRIMARY KEY (src, dst));
CREATE TABLE IF NOT EXISTS library (id TEXT PRIMARY KEY, key TEXT, topic TEXT, subfield TEXT,
  field TEXT);
"""
DECISIONS = ["id", "doi", "title", "status", "note", "date"]

_DATASET = re.compile(
    r"\b(re-?analys[ie]s|data ?sets?|databases?|data records?|multi-?satellite|"
    r"precipitation (analysis|product|climatology)|gridded|archive|GPCP|IMERG|TRMM 3B|"
    r"global precipitation climatology|best track|observational record)\b", re.I)
_MODEL = re.compile(
    r"\b(model description|description of (the |a )?[\w\s-]{0,40}(model|scheme|system)|"
    r"(model|scheme|configuration)s?,? (version|v)\s?\d|global (atmosphere|land|ocean) \d|"
    r"(the )?[A-Z][\w-]*\d(\.\d)* (model|climate model|earth system model)|"
    r"overview of the [\w\s-]{0,30}model|formulation and (evaluation|validation)|"
    r"dynamical core|model configuration)", re.I)
_PROJECT = re.compile(r"\b(intercomparison project|experimental design|experiment design|"
                      r"field (campaign|experiment)|overview of (CMIP|the [\w\s-]*project)|"
                      r"(MIP|campaign)\b.*\b(overview|design|protocol))", re.I)
_REVIEW = re.compile(r"^(a )?review\b|\breview of\b|\bstate of the (art|science)\b", re.I)
DATA_VENUES = ("zenodo", "pangaea", "data repository", "earth system science data",
               "scientific data")


def store_path(lib):
    return lib.index_dir / "field" / "field.sqlite"


def connect(lib) -> sqlite3.Connection:
    p = store_path(lib)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def kind_of(w: dict) -> str:
    title = w.get("title") or ""
    venue = (((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "").lower()
    if w.get("type") == "dataset" or any(v in venue for v in DATA_VENUES) or _DATASET.search(title):
        return "dataset"
    if _PROJECT.search(title):
        return "project"
    if _MODEL.search(title) or ("geoscientific model development" in venue and "model" in title.lower()):
        return "model"
    if w.get("type") == "review" or _REVIEW.search(title) or "reviews of geophysics" in venue:
        return "review"
    return "research"


def _short(oa: str) -> str:
    return (oa or "").rsplit("/", 1)[-1]


def _topic(w: dict) -> tuple[str, str, str]:
    t = w.get("primary_topic") or {}
    return (t.get("display_name") or "", (t.get("subfield") or {}).get("display_name") or "",
            (t.get("field") or {}).get("display_name") or "")


def fetch(client, ids: list[str], select: str = SELECT) -> list[dict]:
    out = []
    for i in range(0, len(ids), 50):
        chunk = [_short(x) for x in ids[i:i + 50]]
        try:
            d = client.get_json(openalex.API, purpose=LOOKUP, params={
                "filter": "openalex:" + "|".join(chunk), "per-page": 50, "select": select})
        except NetError:
            continue
        out.extend(d.get("results", []))
    return out


def score(library_citers: int, cited_by_count: int | None) -> float:
    """Library citers, discounted for works everyone cites (ERA5 says less
    about the library than a paper only this corner of the field cites)."""
    return round(library_citers / math.log10(10 + (cited_by_count or 0)), 3)


def build_backward(lib, client, *, min_cites: int = 3, log=print) -> dict:
    gp = lib.index_dir / "graph.json"
    if not gp.is_file():
        raise ValueError("no citation graph yet: run `litnav graph` first")
    g = json.loads(gp.read_text())
    h = D.Holdings(lib)
    cands = [c for c in g.get("candidates", []) if c.get("cited_by_library", 0) >= min_cites]
    keep, held, maybe = {}, 0, {}
    for c in cands:
        st, k = h.status(c.get("doi", ""), c.get("title", ""), c.get("first_author", ""), c.get("year"))
        if st == "held":
            held += 1
            continue
        keep[c["id"]] = c
        if st == "maybe":
            maybe[c["id"]] = k
    log(f"backward: {len(cands)} works cited by >= {min_cites} library papers; "
        f"{held} held, {len(maybe)} maybe held; fetching {len(keep)}")
    works = fetch(client, list(keep))
    # the library's own works: topics, and ids for links
    lib_ids = {}
    for it in lib.items(purpose=LOOKUP):
        oid = it.meta.get("ids", {}).get("openalex")
        if oid:
            lib_ids[oid] = it.key
    lib_works = fetch(client, list(lib_ids), select="id,primary_topic")
    today = dt.date.today().isoformat()
    con = connect(lib)
    with con:
        con.execute("DELETE FROM library")
        con.executemany("INSERT INTO library VALUES (?,?,?,?,?)",
                        [(_short(w["id"]), lib_ids.get(_short(w["id"]), ""), *_topic(w))
                         for w in lib_works])
        field_ids = set(keep)
        for w in works:
            wid = _short(w["id"])
            c = keep.get(wid, {})
            au = (w.get("authorships") or [{}])[0].get("author", {}).get("display_name", "")
            venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
            ab = clean(from_inverted(w.get("abstract_inverted_index")))[:MAX_LEN]
            pct = (w.get("citation_normalized_percentile") or {}).get("value")
            row = (wid, (w.get("doi") or "").replace("https://doi.org/", "").lower(),
                   w.get("title") or "", w.get("publication_year"), au, venue, w.get("type") or "",
                   kind_of(w), ab, w.get("cited_by_count"), pct, *_topic(w), "backward",
                   c.get("cited_by_library", 0), 0,
                   score(c.get("cited_by_library", 0), w.get("cited_by_count")),
                   maybe.get(wid, ""), today)
            con.execute("INSERT OR REPLACE INTO works VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            for r in w.get("referenced_works") or []:
                r = _short(r)
                if r in field_ids or r in lib_ids:
                    con.execute("INSERT OR IGNORE INTO links VALUES (?,?)", (wid, r))
        # library -> field links, from the graph's citers
        id_of = {k: i for i, k in lib_ids.items()}
        for c in keep.values():
            for k in c.get("citers", []):
                oid = id_of.get(k)
                if oid:
                    con.execute("INSERT OR IGNORE INTO links VALUES (?,?)", (oid, c["id"]))
    n = con.execute("SELECT count(*), sum(abstract<>''), kind FROM works GROUP BY kind").fetchall()
    con.close()
    return {"held": held, "maybe": len(maybe), "fetched": len(works),
            "kinds": {r[2]: r[0] for r in n},
            "with_abstract": sum(r[1] or 0 for r in n)}


# ---- views

def listing(lib, *, kind: str | None = None, n: int = 30, show_decided: bool = False) -> list[dict]:
    con = connect(lib)
    q = "SELECT * FROM works" + (" WHERE kind=?" if kind else "") + " ORDER BY score DESC"
    rows = [dict(r) for r in con.execute(q, (kind,) if kind else ())]
    con.close()
    dec = {r["id"]: r["status"] for r in decisions(lib)}
    if not show_decided:
        rows = [r for r in rows if r["id"] not in dec]
    return rows[:n]


def topics(lib) -> list[dict]:
    """Per OpenAlex topic: works in the field, papers in the library."""
    con = connect(lib)
    f = Counter((r["topic"], r["subfield"]) for r in con.execute("SELECT topic, subfield FROM works"))
    li = Counter((r["topic"], r["subfield"]) for r in con.execute("SELECT topic, subfield FROM library"))
    con.close()
    out = [{"topic": t, "subfield": s, "field_works": f.get((t, s), 0), "library": li.get((t, s), 0)}
           for (t, s) in set(f) | set(li)]
    out.sort(key=lambda r: -(r["field_works"] + r["library"]))
    return out


# ---- the owner's decisions (tracked)

def decisions_path(lib):
    return lib.root / "discovery" / "field.tsv"


def decisions(lib) -> list[dict]:
    p = decisions_path(lib)
    if not p.is_file():
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def decide(lib, ids: list[str], status: str, note: str = "") -> int:
    if status not in ("want", "skip", "note"):
        raise ValueError("status is want, skip or note")
    con = connect(lib)
    rows = {r["id"]: r for r in decisions(lib)}
    n = 0
    for i in ids:
        w = con.execute("SELECT id, doi, title FROM works WHERE id=? OR doi=?",
                        (_short(i), D.norm_doi(i))).fetchone()
        if not w:
            raise ValueError(f"{i} is not in the field store")
        rows[w["id"]] = {"id": w["id"], "doi": w["doi"], "title": w["title"], "status": status,
                         "note": note, "date": dt.date.today().isoformat()}
        n += 1
    con.close()
    p = decisions_path(lib)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, DECISIONS, delimiter="\t", lineterminator="\n")
        wr.writeheader()
        wr.writerows(sorted(rows.values(), key=lambda r: r["id"]))
    return n
