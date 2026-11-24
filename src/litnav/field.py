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
    r"global (atmosphere|land|ocean) \d[\w./ ]*configurations?|"
    r"overview of the [\w\s-]{0,30}model\b|formulation and (evaluation|validation)|"
    r"new dynamical core|dynamical core for|model configurations?\b)", re.I)
# a project or campaign overview, not a study that uses one
_PROJECT = re.compile(
    r"(\b(overview|design|protocol|plan|description|introduc\w*)\b.*\b(intercomparison|project|"
    r"campaign|experiment)s?\b|\b(intercomparison project|campaign|experiment)\b.*\b(overview|"
    r"experimental design|experiment design|protocol)\b|^[\w-]+( \d+)?: an? (field )?"
    r"(campaign|experiment)\b)", re.I)
# evaluations of a dataset or model are research, not the dataset or model
_STUDY = re.compile(r"^(can|does|do|how|is|are|what|why|evaluat\w*|assess\w*|compar\w*|"
                    r"validat\w*|an? (evaluation|assessment|comparison|validation))\b|"
                    r"\b(simulated|represented|in \d+ [\w ]*models|by [\w ]*models)\b", re.I)
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
    cols = {r[1] for r in con.execute("PRAGMA table_info(works)")}
    if "fscore" not in cols:                       # forward score, added with the forward set
        con.execute("ALTER TABLE works ADD COLUMN fscore REAL DEFAULT 0")
    return con


def kind_of(w: dict) -> str:
    title = w.get("title") or ""
    venue = (((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "").lower()
    study = bool(_STUDY.search(title))
    if w.get("type") == "dataset" or any(v in venue for v in DATA_VENUES) or \
            (_DATASET.search(title) and not study):
        return "dataset"
    if _PROJECT.search(title):
        return "project"
    if not study and (_MODEL.search(title) or (
            "geoscientific model development" in venue and "description" in title.lower())):
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
            old = con.execute("SELECT sets, fscore, cites_library FROM works WHERE id=?",
                              (wid,)).fetchone()
            fwd = bool(old and "forward" in (old["sets"] or ""))
            row = (wid, (w.get("doi") or "").replace("https://doi.org/", "").lower(),
                   w.get("title") or "", w.get("publication_year"), au, venue, w.get("type") or "",
                   kind_of(w), ab, w.get("cited_by_count"), pct, *_topic(w),
                   "backward,forward" if fwd else "backward",
                   c.get("cited_by_library", 0), old["cites_library"] if fwd else 0,
                   score(c.get("cited_by_library", 0), w.get("cited_by_count")),
                   maybe.get(wid, ""), today, old["fscore"] if fwd else 0.0)
            con.execute(
                "INSERT OR REPLACE INTO works (id, doi, title, year, first_author, venue, type, "
                "kind, abstract, cited_by_count, percentile, topic, subfield, field, sets, "
                "library_citers, cites_library, score, maybe_held, fetched, fscore) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
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


def weight(cited_by_count: int | None) -> float:
    """What citing one library paper is worth: less for papers everyone cites."""
    return 1 / math.log10(10 + (cited_by_count or 0))


def build_forward(lib, client, *, since: int = 2000, min_lib: int = 5, max_seed_cites: int = 2000,
                  max_works: int = 20000, log=print) -> dict:
    """Works since `since` that cite at least `min_lib` library papers, scored by
    the weights of the library papers they cite. Seeds cited more than
    `max_seed_cites` times are left out: everyone cites them, so they say
    little, and they would cost most of the requests."""
    con = connect(lib)
    lib_ids = dict(con.execute("SELECT id, key FROM library WHERE key<>''").fetchall())
    if not lib_ids:
        raise ValueError("no library works in the field store: run `litnav field build` first")
    cc = {_short(w["id"]): w.get("cited_by_count") or 0
          for w in fetch(client, list(lib_ids), select="id,cited_by_count")}
    seeds = [i for i in lib_ids if cc.get(i, 0) and cc[i] <= max_seed_cites]
    log(f"forward: {len(seeds)} seeds (of {len(lib_ids)}); citing works since {since}")
    wsum, count = Counter(), Counter()
    for n, sid in enumerate(seeds, 1):
        cursor, w = "*", weight(cc[sid])
        while cursor:
            try:
                d = client.get_json(openalex.API, purpose=LOOKUP, params={
                    "filter": f"cites:{sid},from_publication_date:{since}-01-01",
                    "per-page": 200, "select": "id", "cursor": cursor})
            except NetError:
                break
            for r in d.get("results", []):
                cid = _short(r["id"])
                wsum[cid] += w
                count[cid] += 1
            cursor = (d.get("meta") or {}).get("next_cursor") if d.get("results") else None
        if n % 50 == 0:
            log(f"  {n}/{len(seeds)} seeds; {len(count)} citing works so far")
    h = D.Holdings(lib)
    cand = [c for c, k in count.items() if k >= min_lib and c not in lib_ids]
    cand.sort(key=lambda c: -wsum[c])
    cand = cand[:max_works]
    have = {r["id"]: r["sets"] for r in con.execute("SELECT id, sets FROM works")}
    log(f"  {sum(1 for k in count.values() if k >= min_lib)} cite >= {min_lib} library papers; "
        f"fetching {sum(1 for c in cand if c not in have)} new")
    today = dt.date.today().isoformat()
    fetched = {}
    for w in fetch(client, [c for c in cand if c not in have]):
        fetched[_short(w["id"])] = w
    field_ids = set(have) | set(cand)
    held = 0
    with con:
        for cid in cand:
            fs = round(wsum[cid], 3)
            if cid in have:
                sets = have[cid] if "forward" in have[cid] else have[cid] + ",forward"
                con.execute("UPDATE works SET fscore=?, cites_library=?, sets=? WHERE id=?",
                            (fs, count[cid], sets, cid))
                continue
            w = fetched.get(cid)
            if not w:
                continue
            au = (w.get("authorships") or [{}])[0].get("author", {}).get("display_name", "")
            st, k = h.status(w.get("doi") or "", w.get("title") or "", au, w.get("publication_year"))
            if st == "held":
                held += 1
                continue
            venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
            ab = clean(from_inverted(w.get("abstract_inverted_index")))[:MAX_LEN]
            pct = (w.get("citation_normalized_percentile") or {}).get("value")
            con.execute(
                "INSERT OR REPLACE INTO works (id, doi, title, year, first_author, venue, type, kind, "
                "abstract, cited_by_count, percentile, topic, subfield, field, sets, library_citers, "
                "cites_library, score, maybe_held, fetched, fscore) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, (w.get("doi") or "").replace("https://doi.org/", "").lower(),
                 w.get("title") or "", w.get("publication_year"), au, venue, w.get("type") or "",
                 kind_of(w), ab, w.get("cited_by_count"), pct, *_topic(w), "forward", 0,
                 count[cid], 0.0, k if st == "maybe" else "", today, fs))
            for r in w.get("referenced_works") or []:
                r = _short(r)
                if r in field_ids or r in lib_ids:
                    con.execute("INSERT OR IGNORE INTO links VALUES (?,?)", (cid, r))
    n = con.execute("SELECT count(*), sum(abstract<>'') FROM works WHERE sets LIKE '%forward%'"
                    ).fetchone()
    con.close()
    return {"seeds": len(seeds), "citing_works": len(count), "kept": n[0],
            "with_abstract": n[1] or 0, "held": held}


# ---- views

def listing(lib, *, kind: str | None = None, n: int = 30, show_decided: bool = False,
            set_: str | None = None) -> list[dict]:
    con = connect(lib)
    where, args = [], []
    if kind:
        where.append("kind=?")
        args.append(kind)
    if set_:
        where.append("sets LIKE ?")
        args.append(f"%{set_}%")
    q = ("SELECT *, score + coalesce(fscore, 0) AS total FROM works" +
         (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY total DESC")
    rows = [dict(r) for r in con.execute(q, args)]
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


# ---- embeddings, search and the topic map
# Field works are embedded as title + abstract; library papers the same way
# (public abstract, else the one found in the text, else the summary) so the
# two sit in one space. Embedding is local; vectors stay in the field store.
# Cluster labels use field works' titles and abstracts and library titles
# only, so no words of a licensed paper's text show up in them.

VEC_SCHEMA = "CREATE TABLE IF NOT EXISTS vecs (id TEXT PRIMARY KEY, src TEXT, vhash TEXT, vec BLOB)"
STOP = set("""a an the of and or in on for to with by from at as is are was were be been being this
that these those it its we our their which using use used based new over under between into than
via about during after before not no also can may more most such both two three one study results
show shows shown paper here using model models data analysis""".split())


def _texts(lib, con):
    """(id, src, text) for every field work and library paper to embed."""
    from . import abstracts
    from . import index as I
    out = [(r["id"], "field", f"{r['title']}\n{r['abstract']}")
           for r in con.execute("SELECT id, title, abstract FROM works")]
    icon = I.connect(lib, readonly=True)
    for r in con.execute("SELECT id, key FROM library"):
        if not r["key"]:
            continue
        it = lib.get(r["key"])
        if not it.allowed(LOOKUP):
            continue
        row = icon.execute("SELECT abstract, summary FROM items WHERE key=?", (it.key,)).fetchone()
        body = abstracts.public(it) or (row["abstract"] if row else "") or (row["summary"] if row else "")
        out.append((r["id"], "library", f"{it.title}\n{body}"))
    icon.close()
    return out


def embed(lib, embedder, *, log=print) -> dict:
    from .embed import vhash
    con = connect(lib)
    con.execute(VEC_SCHEMA)
    have = dict(con.execute("SELECT id, vhash FROM vecs").fetchall())
    todo = [(i, s, t, vhash(embedder.name, t)) for i, s, t in _texts(lib, con)]
    todo = [x for x in todo if have.get(x[0]) != x[3]]
    log(f"field: {len(todo)} to embed")
    for i in range(0, len(todo), 256):
        part = todo[i:i + 256]
        m = embedder.docs([t for _, _, t, _ in part])
        with con:
            con.executemany("INSERT OR REPLACE INTO vecs VALUES (?,?,?,?)",
                            [(wid, s, h, m[j].tobytes()) for j, (wid, s, _, h) in enumerate(part)])
        log(f"  embedded {min(i + 256, len(todo))}/{len(todo)}")
    n = con.execute("SELECT count(*) FROM vecs").fetchone()[0]
    con.close()
    return {"embedded": len(todo), "total": n}


def _matrix(con):
    import numpy as np
    con.execute(VEC_SCHEMA)
    rows = con.execute("SELECT id, src, vec FROM vecs ORDER BY id").fetchall()
    if not rows:
        raise ValueError("no field vectors yet: run `litnav field embed`")
    m = np.frombuffer(b"".join(r["vec"] for r in rows), dtype="float32").reshape(len(rows), -1)
    m = m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
    return [r["id"] for r in rows], [r["src"] for r in rows], m


def search(lib, embedder, query: str, *, k: int = 15) -> list[dict]:
    import numpy as np
    con = connect(lib)
    ids, srcs, m = _matrix(con)
    q = embedder.query(query)
    s = m @ (q / max(np.linalg.norm(q), 1e-9))
    out = []
    for j in np.argsort(-s)[:k]:
        if srcs[j] == "library":
            r = con.execute("SELECT key FROM library WHERE id=?", (ids[j],)).fetchone()
            out.append({"id": ids[j], "held": r["key"], "score": round(float(s[j]), 3)})
        else:
            r = dict(con.execute("SELECT id, doi, title, year, first_author, kind, abstract "
                                 "FROM works WHERE id=?", (ids[j],)).fetchone())
            out.append({**r, "held": "", "score": round(float(s[j]), 3)})
    con.close()
    return out


def kmeans(m, k: int, *, iters: int = 50, seed: int = 0):
    """Spherical k-means (cosine), k-means++ start; deterministic for a seed."""
    import numpy as np
    rng = np.random.default_rng(seed)
    c = [m[rng.integers(len(m))]]
    for _ in range(1, k):
        d = 1 - np.max(m @ np.array(c).T, axis=1)
        d = np.clip(d, 0, None) ** 2
        c.append(m[rng.choice(len(m), p=d / d.sum())])
    c = np.array(c)
    lab = None
    for _ in range(iters):
        new = np.argmax(m @ c.T, axis=1)
        if lab is not None and (new == lab).all():
            break
        lab = new
        for j in range(k):
            pts = m[lab == j]
            if len(pts):
                v = pts.sum(0)
                c[j] = v / max(np.linalg.norm(v), 1e-9)
    return lab, c


def _words(t: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9-]{2,}", t.lower()) if w not in STOP]


def topic_map(lib, *, k: int = 40, seed: int = 0) -> list[dict]:
    """Clusters of field works and library papers together: per cluster, its
    distinctive words, how many field works and held papers it has, and its
    top unheld works by score."""
    import numpy as np
    con = connect(lib)
    ids, srcs, m = _matrix(con)
    lab, cent = kmeans(m, k, seed=seed)
    works = {r["id"]: dict(r) for r in con.execute(
        "SELECT id, title, abstract, year, first_author, score, kind, library_citers FROM works")}
    keys = dict(con.execute("SELECT id, key FROM library").fetchall())
    con.close()
    docs = []
    for i, s in zip(ids, srcs):
        if s == "field":
            w = works.get(i, {})
            docs.append(_words(f"{w.get('title', '')} {w.get('abstract', '')}"))
        else:
            k_ = keys.get(i, "")
            docs.append(_words(lib.get(k_).title if k_ else ""))
    df = Counter(w for d in docs for w in set(d))
    n = len(docs)
    out = []
    for j in range(k):
        idx = np.where(lab == j)[0]
        if not len(idx):
            continue
        tf = Counter(w for i in idx for w in docs[i])
        terms = sorted(tf, key=lambda w: -tf[w] * math.log(n / (1 + df[w])))[:8]
        f = [ids[i] for i in idx if srcs[i] == "field"]
        held = [keys.get(ids[i], "") for i in idx if srcs[i] == "library"]
        # closest to the centre first, for the held papers
        sims = {ids[i]: float(m[i] @ cent[j]) for i in idx}
        held.sort(key=lambda kk: -max((sims[i] for i, x in keys.items() if x == kk and i in sims),
                                      default=0))
        top = sorted((works[i] for i in f if i in works), key=lambda w: -(w["score"] or 0))[:5]
        out.append({"cluster": j, "terms": terms, "field_works": len(f), "held": len(held),
                    "held_share": round(len(held) / len(idx), 2),
                    "held_examples": held[:4],
                    "top_unheld": [{"id": w["id"], "year": w["year"], "first_author": w["first_author"],
                                    "title": w["title"], "kind": w["kind"],
                                    "library_citers": w["library_citers"]} for w in top]})
    out.sort(key=lambda c: c["held_share"])
    return out


def map_markdown(clusters: list[dict]) -> str:
    """The topic map as a page: thinnest-held clusters first."""
    lines = ["# The field around the library: topic map", "",
             "Clusters of field works (abstracts from OpenAlex) and library papers, embedded "
             "together. Sorted by the share held, so the least-covered topics come first. "
             "Words are the cluster's most distinctive; 'top unheld' ranks works by how many "
             "library papers cite them, discounted for very widely cited ones.", "",
             "| # | words | field works | held | held share |", "|---|---|---|---|---|"]
    for c in clusters:
        lines.append(f"| {c['cluster']} | {', '.join(c['terms'])} | {c['field_works']} | "
                     f"{c['held']} | {c['held_share']:.0%} |")
    for c in clusters:
        lines += ["", f"## {c['cluster']}: {', '.join(c['terms'][:5])}", "",
                  f"{c['field_works']} field works, {c['held']} held "
                  f"({c['held_share']:.0%}). Held, e.g.: "
                  f"{', '.join('`' + k + '`' for k in c['held_examples']) or 'none'}.", ""]
        for w in c["top_unheld"]:
            lines.append(f"- {w['year']} {w['first_author']}: {w['title']} "
                         f"({w['kind']}; cited by {w['library_citers']} library papers; {w['id']})")
    return "\n".join(lines) + "\n"
