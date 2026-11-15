"""The derived index: <index_dir>/index.sqlite. Deletable; `litnav index
--rebuild` recreates it from the files.

Tables
  items       one row per item: catalogue fields, sharing, signature
  items_fts   FTS5 over title, authors, tags, summary (paper-level keyword search)
  sections    detected sections per item
  chunks      chunks (text, offsets, sharing level copied from the item)
  chunks_fts  FTS5 over chunk header + text
  vectors     embedding BLOBs keyed by hash(model, embedded text)
  edges       citation edges (src key -> dst key), from the graph step
  state       schema/chunker versions

Updates are incremental: an item is re-indexed only when its signature
(sizes and mtimes of ref.bib, meta.toml, text.txt, summary.json, plus the
chunker version) changes. Sharing is copied onto every chunk row so search
can filter at chunk level.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import chunks as C
from . import keys
from . import sections as S

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS items (
    key TEXT PRIMARY KEY, title TEXT, authors TEXT, first_author TEXT, year INTEGER,
    journal TEXT, doi TEXT, tags TEXT, sharing TEXT, has_pdf INTEGER, has_text INTEGER,
    summary TEXT, topic TEXT, abstract TEXT, sections_detected INTEGER, n_chunks INTEGER,
    sig TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    key UNINDEXED, title, authors, tags, summary, tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS sections (
    key TEXT, slug TEXT, title TEXT, level INTEGER, kind TEXT, start INTEGER, "end" INTEGER,
    ord INTEGER, PRIMARY KEY (key, slug));
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, key TEXT, section TEXT, section_title TEXT, kind TEXT, ord INTEGER,
    start INTEGER, "end" INTEGER, text TEXT, header TEXT, sharing TEXT, vhash TEXT);
CREATE INDEX IF NOT EXISTS chunks_key ON chunks(key);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    id UNINDEXED, key UNINDEXED, header, text, tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS vectors (vhash TEXT PRIMARY KEY, model TEXT, dim INTEGER, vec BLOB);
CREATE TABLE IF NOT EXISTS paper_vectors (key TEXT PRIMARY KEY, vhash TEXT);
CREATE TABLE IF NOT EXISTS edges (src TEXT, dst TEXT, PRIMARY KEY (src, dst));
CREATE TABLE IF NOT EXISTS backlinks (src TEXT, dst TEXT, PRIMARY KEY (src, dst));
"""


def db_path(lib) -> Path:
    return lib.index_dir / "index.sqlite"


def connect(lib, readonly: bool = False) -> sqlite3.Connection:
    p = db_path(lib)
    if readonly:
        if not p.exists():
            raise FileNotFoundError("no index yet: run `litnav index`")
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, check_same_thread=False)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(p, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        con.execute("INSERT OR IGNORE INTO state VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def writer(lib):
    con = connect(lib)
    try:
        with con:
            yield con
    finally:
        con.close()


def signature(it) -> str:
    parts = [f"c{C.VERSION}s{S.VERSION}"]
    for p in (it.bib_path, it.meta_path, it.text_path, it.summary_path):
        try:
            st = p.stat()
            parts.append(f"{st.st_size}:{int(st.st_mtime_ns)}")
        except FileNotFoundError:
            parts.append("-")
    parts.append("pdf" if it.has_pdf else "nopdf")
    return "|".join(parts)


def _abstract(parsed: S.Parsed) -> str:
    for s in parsed.sections:
        if s.kind == "abstract":
            t = parsed.text[s.start:s.end]
            nl = t.find("\n")
            return " ".join(t[nl + 1 if 0 <= nl < 40 else 0:].split())[:3000]
    return ""


def index_item(con, it) -> int:
    """(Re)index one item; returns the number of chunks."""
    key = it.key
    con.execute("DELETE FROM items WHERE key=?", (key,))
    con.execute("DELETE FROM items_fts WHERE key=?", (key,))
    con.execute("DELETE FROM sections WHERE key=?", (key,))
    con.execute("DELETE FROM chunks WHERE key=?", (key,))
    con.execute("DELETE FROM chunks_fts WHERE key=?", (key,))
    summ = it.summary() or {}
    authors = it.authors
    first = keys.surname(authors[0]) if authors else ""
    parsed, cs = None, []
    # confidential items: catalogue only, text never indexed
    if it.has_text and it.sharing != "confidential":
        manual = it.meta.get("files", {}).get("sections") == "manual"
        parsed = S.parse(it.text(), manual=manual)
        cs = C.chunk(key, parsed)
    year = int(it.year) if str(it.year).isdigit() else None
    con.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        key, it.title, "; ".join(authors), first, year, it.journal, it.doi,
        ",".join(it.tags), it.sharing, int(it.has_pdf), int(it.has_text),
        summ.get("summary", ""), summ.get("topic", ""),
        _abstract(parsed) if parsed else "", int(bool(parsed and parsed.detected)), len(cs),
        signature(it)))
    con.execute("INSERT INTO items_fts VALUES (?,?,?,?,?)", (
        key, it.title, "; ".join(authors), " ".join(it.tags),
        " ".join(filter(None, [summ.get("topic", ""), summ.get("summary", "")]))))
    if parsed:
        for i, s in enumerate(parsed.sections):
            con.execute("INSERT OR REPLACE INTO sections VALUES (?,?,?,?,?,?,?,?)",
                        (key, s.slug, s.title, s.level, s.kind, s.start, s.end, i))
    for c in cs:
        h = C.header(first, it.year, it.title, c.section_title)
        con.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            c.id, key, c.section, c.section_title, c.kind, c.ord, c.start, c.end, c.text, h,
            it.sharing, None))
        con.execute("INSERT INTO chunks_fts VALUES (?,?,?,?)", (c.id, key, h, c.text))
    return len(cs)


def update(lib, *, rebuild: bool = False, keys_=None, log=print) -> dict:
    stats = {"indexed": 0, "unchanged": 0, "removed": 0, "chunks": 0}
    if rebuild and db_path(lib).exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_path(lib)) + suffix)
            if p.exists():
                p.unlink()
    with writer(lib) as con:
        have = {r["key"]: r["sig"] for r in con.execute("SELECT key, sig FROM items")}
        present = set(lib.keys())
        for k in set(have) - present:
            for t in ("items", "items_fts", "sections", "chunks", "chunks_fts", "paper_vectors"):
                con.execute(f"DELETE FROM {t} WHERE key=?", (k,))
            stats["removed"] += 1
        todo = [lib.get(k) for k in (keys_ or sorted(present))]
        for n, it in enumerate(todo, 1):
            if have.get(it.key) == signature(it) and not keys_:
                stats["unchanged"] += 1
                continue
            stats["chunks"] += index_item(con, it)
            stats["indexed"] += 1
            if n % 100 == 0:
                log(f"  indexed {n}/{len(todo)}")
                con.commit()
        con.execute("INSERT OR REPLACE INTO state VALUES ('chunker', ?)", (str(C.VERSION),))
    return stats


def section_text(con, key: str, slug: str, lib) -> tuple[dict, str] | None:
    r = con.execute("SELECT * FROM sections WHERE key=? AND slug=?", (key, slug)).fetchone()
    if not r:
        return None
    it = lib.get(key)
    parsed = S.parse(it.text(), manual=it.meta.get("files", {}).get("sections") == "manual")
    return dict(r), parsed.text[r["start"]:r["end"]]


def to_json(row) -> str:
    return json.dumps(dict(row))
