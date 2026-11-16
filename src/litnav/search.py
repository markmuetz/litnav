"""Search the index: keyword (FTS5/BM25), semantic (embeddings), hybrid
(reciprocal rank fusion), at passage or paper level, with filters.

Every function takes a `purpose` and only returns chunks whose item's
sharing level allows it. Results carry chunk IDs, offsets and verbatim text.

Query syntax: words, plus "quoted phrases", which are required (exact names,
acronyms, case studies). Unquoted words are optional and ranked by BM25; FTS5
operators in user input are neutralised by quoting every token.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import keys
from . import sharing as SH

RRF_K = 60
POOL = 100


@dataclass
class Filters:
    year_from: int | None = None
    year_to: int | None = None
    tag: str | None = None
    author: str | None = None
    keys: list[str] | None = None
    project: str | None = None
    kinds: tuple = ("abstract", "body", "front", "caption", "appendix", "window")

    def sql(self, alias="i") -> tuple[str, list]:
        w, a = [], []
        if self.year_from:
            w.append(f"{alias}.year >= ?"); a.append(self.year_from)
        if self.year_to:
            w.append(f"{alias}.year <= ?"); a.append(self.year_to)
        if self.tag:
            w.append(f"(',' || {alias}.tags || ',') LIKE ?"); a.append(f"%,{self.tag},%")
        if self.author:
            w.append(f"{alias}.authors LIKE ?"); a.append(f"%{self.author}%")
        if self.keys is not None:
            w.append(f"{alias}.key IN ({','.join('?' * len(self.keys)) or 'NULL'})"); a += list(self.keys)
        return (" AND ".join(w), a)


def parse_query(q: str) -> tuple[list[str], list[str]]:
    """(required phrases, optional words)."""
    phrases = [p.strip() for p in re.findall(r'"([^"]+)"', q) if p.strip()]
    rest = re.sub(r'"[^"]*"', " ", q)
    words = [w for w in re.findall(r"[\w][\w\-./]*", rest)
             if w.lower() not in keys.STOP and w.upper() not in ("AND", "OR", "NOT", "NEAR")]
    return phrases, words


def _fts(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def fts_query(q: str) -> str | None:
    phrases, words = parse_query(q)
    req = " AND ".join(_fts(p) for p in phrases)
    opt = " OR ".join(_fts(w) for w in words)
    if req and opt:
        return f"{req} AND ({opt})"
    return req or opt or None


def _allowed_levels(purpose: str) -> list[str]:
    return [l for l in SH.LEVELS if SH.allow(l, purpose)]


def _row(r, score, how) -> dict:
    d = dict(r)
    d["score"] = round(float(score), 4)
    d["how"] = how
    return d


_SELECT = ("SELECT c.id, c.key, c.section, c.section_title, c.kind, c.start, c.\"end\", c.text, "
           "i.title, i.year, i.authors, i.first_author FROM chunks c JOIN items i ON i.key=c.key")


def keyword(con, q: str, *, k: int = 10, filters: Filters | None = None,
            purpose: str = SH.LOCAL, pool: int = POOL) -> list[dict]:
    fq = fts_query(q)
    if not fq:
        return []
    f = filters or Filters()
    w, a = f.sql()
    lv = _allowed_levels(purpose)
    sql = (f"{_SELECT} JOIN (SELECT id, bm25(chunks_fts, 0, 0, 2.0, 1.0) AS s FROM chunks_fts "
           f"WHERE chunks_fts MATCH ?) m ON m.id=c.id "
           f"WHERE c.sharing IN ({','.join('?' * len(lv))}) "
           f"AND c.kind IN ({','.join('?' * len(f.kinds))})" + (f" AND {w}" if w else "") +
           " ORDER BY m.s LIMIT ?")
    rows = con.execute(sql, [fq, *lv, *f.kinds, *a, pool]).fetchall()
    # bm25 is lower-is-better; report as a positive score
    return [_row(r, -i, "keyword") for i, r in enumerate(rows)][: max(k, pool)]


def semantic(con, q: str, embedder, *, k: int = 10, filters: Filters | None = None,
             purpose: str = SH.LOCAL, pool: int = POOL) -> list[dict]:
    import numpy as np
    from . import embed
    ids, _keys, lv, m = embed.matrix(con, embedder.name, "chunks")
    if not ids:
        return []
    qv = embedder.query(q)
    qv = qv / max(float(np.linalg.norm(qv)), 1e-9)
    sims = m @ qv
    mask = embed.allowed_mask(lv, purpose)
    sims = np.where(mask, sims, -np.inf)
    f = filters or Filters()
    w, a = f.sql()
    allowed_keys = None
    if w:
        allowed_keys = {r[0] for r in con.execute(f"SELECT key FROM items i WHERE {w}", a)}
    order = np.argsort(-sims)
    out = []
    for j in order:
        if not np.isfinite(sims[j]):
            break
        if allowed_keys is not None and _keys[j] not in allowed_keys:
            continue
        out.append((ids[j], float(sims[j])))
        if len(out) >= pool * 2:
            break
    if not out:
        return []
    byid = {r["id"]: r for r in con.execute(
        f"{_SELECT} WHERE c.id IN ({','.join('?' * len(out))})", [i for i, _ in out])}
    res = [_row(byid[i], s, "semantic") for i, s in out if i in byid and byid[i]["kind"] in f.kinds]
    return res[:pool]


def _phrases_ok(text: str, phrases: list[str]) -> bool:
    t = " ".join(keys.words(text))
    return all(" ".join(keys.words(p)) in t for p in phrases)


def hybrid(con, q: str, embedder, *, k: int = 10, filters: Filters | None = None,
           purpose: str = SH.LOCAL) -> list[dict]:
    phrases, _ = parse_query(q)
    kw = keyword(con, q, k=POOL, filters=filters, purpose=purpose)
    sem = semantic(con, re.sub(r'"', "", q), embedder, k=POOL, filters=filters, purpose=purpose)
    if phrases:
        sem = [r for r in sem if _phrases_ok(r["text"], phrases)]
    score, rows = {}, {}
    for lst in (kw, sem):
        for rank, r in enumerate(lst):
            score[r["id"]] = score.get(r["id"], 0) + 1 / (RRF_K + rank + 1)
            rows.setdefault(r["id"], r)
    ranked = sorted(score, key=lambda i: -score[i])
    out = []
    for i in ranked[:k]:
        r = dict(rows[i])
        r["score"] = round(score[i], 5)
        r["how"] = "hybrid"
        out.append(r)
    return out


def search(con, q: str, *, embedder=None, mode: str = "hybrid", k: int = 10,
           filters: Filters | None = None, purpose: str = SH.LOCAL) -> list[dict]:
    if mode == "keyword" or embedder is None:
        return keyword(con, q, k=k, filters=filters, purpose=purpose)[:k]
    if mode == "semantic":
        return semantic(con, q, embedder, k=k, filters=filters, purpose=purpose)[:k]
    return hybrid(con, q, embedder, k=k, filters=filters, purpose=purpose)


def papers(con, q: str, *, embedder=None, k: int = 10, filters: Filters | None = None,
           purpose: str = SH.LOCAL) -> list[dict]:
    """Paper-level: fuse paper-vector similarity, item keyword match and the
    papers' best passages."""
    lv = _allowed_levels(purpose)
    f = filters or Filters()
    w, a = f.sql()
    score: dict[str, float] = {}

    def add(keys_in_order):
        for rank, key in enumerate(dict.fromkeys(keys_in_order)):
            score[key] = score.get(key, 0) + 1 / (RRF_K + rank + 1)
    fq = fts_query(q)
    if fq:
        rows = con.execute(
            f"SELECT i.key FROM items_fts JOIN items i ON i.key=items_fts.key WHERE items_fts MATCH ? "
            f"AND i.sharing IN ({','.join('?' * len(lv))})" + (f" AND {w}" if w else "") +
            " ORDER BY bm25(items_fts, 0, 3.0, 2.0, 1.0, 1.0) LIMIT ?", [fq, *lv, *a, POOL]).fetchall()
        add([r[0] for r in rows])
    if embedder is not None:
        import numpy as np
        from . import embed
        pk, _, plv, m = embed.matrix(con, embedder.name, "papers")
        if pk:
            qv = embedder.query(q)
            sims = m @ (qv / max(float(np.linalg.norm(qv)), 1e-9))
            sims = np.where(embed.allowed_mask(plv, purpose), sims, -np.inf)
            add([pk[j] for j in np.argsort(-sims)[:POOL] if np.isfinite(sims[j])])
    passages = search(con, q, embedder=embedder, k=POOL, filters=filters, purpose=purpose)
    add([p["key"] for p in passages])
    best = {}
    for p in passages:
        best.setdefault(p["key"], p)
    ranked = sorted(score, key=lambda x: -score[x])[:k]
    out = []
    for key in ranked:
        r = con.execute("SELECT key, title, year, authors, topic FROM items WHERE key=?", (key,)).fetchone()
        if r is None:
            continue
        d = dict(r)
        d["score"] = round(score[key], 5)
        if key in best:
            d["best_passage"] = {x: best[key][x] for x in ("id", "section_title", "text")}
        out.append(d)
    return out


def similar(con, key: str, embedder_name: str, *, k: int = 10, purpose: str = SH.LOCAL) -> list[dict]:
    import numpy as np
    from . import embed
    pk, _, plv, m = embed.matrix(con, embedder_name, "papers")
    if key not in pk:
        return []
    i = pk.index(key)
    sims = m @ m[i]
    sims = np.where(embed.allowed_mask(plv, purpose), sims, -np.inf)
    sims[i] = -np.inf
    out = []
    for j in np.argsort(-sims)[:k]:
        if not np.isfinite(sims[j]):
            break
        r = con.execute("SELECT key, title, year, authors FROM items WHERE key=?", (pk[j],)).fetchone()
        d = dict(r)
        d["score"] = round(float(sims[j]), 4)
        out.append(d)
    return out


def notes(con, q: str, *, embedder=None, k: int = 10, purpose: str = SH.LOCAL,
          kinds: tuple = ("note", "concept", "topic")) -> list[dict]:
    """Search the user's own notes (notes/, concepts/, topics/): keyword and,
    with an embedder, semantic, fused by reciprocal rank. Only passages whose
    note's level allows `purpose`."""
    lv = _allowed_levels(purpose)
    cols = "n.id, n.path, n.kind, n.about, n.title, n.section, n.text"
    where = (f"n.sharing IN ({','.join('?' * len(lv))}) "
             f"AND n.kind IN ({','.join('?' * len(kinds))})")
    score, rows = {}, {}

    def add(lst):
        for rank, r in enumerate(lst):
            score[r["id"]] = score.get(r["id"], 0) + 1 / (RRF_K + rank + 1)
            rows.setdefault(r["id"], dict(r))
    phrases, _ = parse_query(q)
    fq = fts_query(q)
    if fq:
        add(con.execute(
            f"SELECT {cols} FROM notes n JOIN (SELECT id, bm25(notes_fts, 0, 0, 2.0, 1.0) AS s "
            f"FROM notes_fts WHERE notes_fts MATCH ?) m ON m.id=n.id WHERE {where} "
            f"ORDER BY m.s LIMIT ?", [fq, *lv, *kinds, POOL]).fetchall())
    if embedder is not None:
        import numpy as np
        from . import embed
        ids, _, nlv, m = embed.matrix(con, embedder.name, "notes")
        if ids:
            qv = embedder.query(re.sub(r'"', "", q))
            sims = m @ (qv / max(float(np.linalg.norm(qv)), 1e-9))
            sims = np.where(embed.allowed_mask(nlv, purpose), sims, -np.inf)
            top = [ids[j] for j in np.argsort(-sims)[:POOL] if np.isfinite(sims[j])]
            if top:
                got = {r["id"]: r for r in con.execute(
                    f"SELECT {cols} FROM notes n WHERE n.id IN ({','.join('?' * len(top))}) "
                    f"AND {where}", [*top, *lv, *kinds])}
                sem = [got[i] for i in top if i in got]
                if phrases:
                    sem = [r for r in sem if _phrases_ok(r["text"], phrases)]
                add(sem)
    out = []
    for i in sorted(score, key=lambda x: -score[x])[:k]:
        r = rows[i]
        r["score"] = round(score[i], 5)
        out.append(r)
    return out
