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
  notes       passages of the user's notes/, concepts/, topics/ (notes.py),
              with each file's sharing level
  notes_fts   FTS5 over note passage header + text
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
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY, path TEXT, kind TEXT, about TEXT, title TEXT, section TEXT, ord INTEGER,
    text TEXT, header TEXT, sharing TEXT, vhash TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    id UNINDEXED, path UNINDEXED, header, text, tokenize = 'unicode61 remove_diacritics 2');
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
        con = sqlite3.connect(p, check_same_thread=False, timeout=60)
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        con.execute("INSERT OR IGNORE INTO state VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
    con.row_factory = sqlite3.Row
    return con


def bump(con) -> None:
    """Advance the index generation when the set of embedded passages or
    papers changes: embed.matrix's cache keys on it. (A sharing change does
    not need it: the sharing mask is computed for every query.)"""
    con.execute("INSERT INTO state VALUES ('gen', '1') ON CONFLICT(k) DO UPDATE "
                "SET v = CAST(v AS INTEGER) + 1")


def generation(con) -> str:
    r = con.execute("SELECT v FROM state WHERE k='gen'").fetchone()
    return r[0] if r else "0"


class Reader:
    """One read-only connection for a long-lived process (web app, MCP
    server), reopened when the index file is replaced (`index --rebuild`)."""

    def __init__(self, lib):
        import threading
        self.lib, self._con, self._ino = lib, None, None
        self._lock = threading.Lock()

    def __call__(self) -> sqlite3.Connection:
        with self._lock:
            return self._get()

    def _get(self) -> sqlite3.Connection:
        try:
            ino = db_path(self.lib).stat().st_ino
        except FileNotFoundError:
            ino = None
        if self._con is None or ino != self._ino:
            if self._con is not None:
                self._con.close()
            self._con, self._ino = connect(self.lib, readonly=True), ino
        return self._con


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
    for p in (it.bib_path, it.meta_path, it.text_path, it.summary_path, it.dir / "abstract.json"):
        try:
            st = p.stat()
            parts.append(f"{st.st_size}:{int(st.st_mtime_ns)}")
        except FileNotFoundError:
            parts.append("-")
    parts.append("pdf" if it.has_pdf else "nopdf")
    parts.append(it.sharing)             # catches a changed default_sharing too
    return "|".join(parts)


def _public_abstract(it) -> str:
    from . import abstracts
    return abstracts.public(it)


def _abstract(parsed: S.Parsed) -> str:
    for s in parsed.sections:
        if s.kind == "abstract":
            t = parsed.text[s.start:s.end]
            nl = t.find("\n")
            return " ".join(t[nl + 1 if 0 <= nl < 40 else 0:].split())[:3000]
    return ""


def index_item(con, it) -> int:
    """(Re)index one item; returns the number of chunks."""
    from .embed import vhash
    model = it.lib.config["search"]["embedding_model"]
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
        (_abstract(parsed) if parsed else "") or _public_abstract(it),
        int(bool(parsed and parsed.detected)), len(cs),
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
        # the vector hash is set now, so a chunk whose text already has a vector
        # stays in semantic search without waiting for `embed.update`
        con.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            c.id, key, c.section, c.section_title, c.kind, c.ord, c.start, c.end, c.text, h,
            it.sharing, vhash(model, f"{h}\n{c.text}")))
        con.execute("INSERT INTO chunks_fts VALUES (?,?,?,?)", (c.id, key, h, c.text))
    return len(cs)


def update(lib, *, rebuild: bool = False, keys_=None, log=print) -> dict:
    stats = {"indexed": 0, "unchanged": 0, "removed": 0, "chunks": 0}
    if rebuild and db_path(lib).exists():
        # In place, not by deleting the file: open readers (web app, MCP
        # server) keep a valid connection, and stored vectors (keyed by a hash
        # of their text) are kept, so nothing unchanged is embedded again.
        with writer(lib) as con:
            for t in ("items", "items_fts", "sections", "chunks", "chunks_fts", "paper_vectors",
                      "notes", "notes_fts", "backlinks"):
                con.execute(f"DROP TABLE IF EXISTS {t}")
            con.execute("DELETE FROM state WHERE k='chunker'")
        with writer(lib) as con:
            con.executescript(SCHEMA)
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
            before = con.execute("SELECT group_concat(vhash, ',') FROM "
                                 "(SELECT vhash FROM chunks WHERE key=? ORDER BY id)", (it.key,)).fetchone()[0]
            stats["chunks"] += index_item(con, it)
            after = con.execute("SELECT group_concat(vhash, ',') FROM "
                                "(SELECT vhash FROM chunks WHERE key=? ORDER BY id)", (it.key,)).fetchone()[0]
            stats["indexed"] += 1
            if before != after:
                stats["vectors_changed"] = True
            if n % 100 == 0:
                log(f"  indexed {n}/{len(todo)}")
                con.commit()
        con.execute("INSERT OR REPLACE INTO state VALUES ('chunker', ?)", (str(C.VERSION),))
        if index_notes(con, lib):
            stats["vectors_changed"] = True
        stats["notes"] = con.execute("SELECT count(*) FROM notes").fetchone()[0]
        if stats.pop("vectors_changed", False) or stats["removed"]:
            bump(con)                    # the set of embedded passages changed
    return stats


def index_notes(con, lib) -> bool:
    """Re-index every note (a few hundred files at most, so all of them each
    time: a note's level depends on the items it cites, which change without
    the note changing). True if the set of passages changed."""
    from . import notes as N
    from .embed import vhash
    model = lib.config["search"]["embedding_model"]
    before = con.execute("SELECT group_concat(vhash || sharing, ',') FROM "
                         "(SELECT vhash, sharing FROM notes ORDER BY id)").fetchone()[0]
    con.execute("DELETE FROM notes")
    con.execute("DELETE FROM notes_fts")
    for f in N.files(lib):
        text = f.read_text("utf-8", "replace")
        lv = N.level(lib, N.rel(lib, f), text)
        for p in N.passages(lib, f, text):
            con.execute("INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                p.id, p.path, p.kind, p.about, p.title, p.section, p.ord, p.text, p.header, lv,
                vhash(model, f"{p.header}\n{p.text}")))
            con.execute("INSERT INTO notes_fts VALUES (?,?,?,?)", (p.id, p.path, p.header, p.text))
    after = con.execute("SELECT group_concat(vhash || sharing, ',') FROM "
                        "(SELECT vhash, sharing FROM notes ORDER BY id)").fetchone()[0]
    return before != after


def section_text(con, key: str, slug: str, lib) -> tuple[dict, str] | None:
    r = con.execute("SELECT * FROM sections WHERE key=? AND slug=?", (key, slug)).fetchone()
    if not r:
        return None
    it = lib.get(key)
    parsed = S.parse(it.text(), manual=it.meta.get("files", {}).get("sections") == "manual")
    return dict(r), parsed.text[r["start"]:r["end"]]


def to_json(row) -> str:
    return json.dumps(dict(row))
