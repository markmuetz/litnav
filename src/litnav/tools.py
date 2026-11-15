"""The read-only literature tools, as plain functions.

The MCP server, the web app's Ask view and the API back end all call these,
so every agent sees the same tools with the same sharing rules. Each takes a
`purpose`: `send_out` when the result goes to a model (the default for
agents), `serve_meta`/`serve_text` when serving someone else.

Results are capped in size; passages carry chunk IDs, offsets and verbatim
text so a claim can be checked.
"""
from __future__ import annotations

from . import index, refs, search
from . import sharing as S

MAX_K = 25
MAX_SECTION_CHARS = 12000


class Tools:
    def __init__(self, lib, purpose: str = S.SEND_OUT, embedder=None):
        self.lib, self.purpose = lib, purpose
        self.con = index.connect(lib, readonly=True)
        self._embedder = embedder
        self._emb_loaded = embedder is not None

    @property
    def embedder(self):
        if not self._emb_loaded:
            self._emb_loaded = True
            from .commands.searching import embedder_for
            try:
                self._embedder = embedder_for(self.lib)
            except Exception:
                self._embedder = None
        return self._embedder

    def _item_ok(self, key: str, need_text: bool = False):
        k = self.lib.resolve(key)
        if not k:
            raise ValueError(f"no paper {key!r}")
        it = self.lib.get(k)
        need = self.purpose
        if self.purpose in (S.SERVE_META, S.SERVE_TEXT) and need_text:
            need = S.SERVE_TEXT
        if not it.allowed(need):
            raise PermissionError(f"{k} is not available for this use ({it.sharing})")
        return it

    # -- tools

    def search_literature(self, query: str, k: int = 10, mode: str = "hybrid", papers: bool = False,
                          year_from: int | None = None, year_to: int | None = None,
                          tag: str | None = None, author: str | None = None) -> list[dict]:
        k = max(1, min(int(k), MAX_K))
        f = search.Filters(year_from=year_from, year_to=year_to, tag=tag, author=author)
        purpose = S.SERVE_TEXT if self.purpose == S.SERVE_META and not papers else self.purpose
        if papers:
            return search.papers(self.con, query, embedder=self.embedder, k=k, filters=f, purpose=purpose)
        res = search.search(self.con, query, embedder=self.embedder, mode=mode, k=k, filters=f,
                            purpose=purpose)
        keep = ("id", "key", "title", "year", "authors", "section_title", "kind", "start", "end",
                "text", "score")
        return [{x: r.get(x) for x in keep} for r in res]

    def get_chunk(self, chunk_id: str) -> dict:
        key = chunk_id.split("#", 1)[0]
        self._item_ok(key, need_text=True)
        r = self.con.execute("SELECT id, key, section, section_title, kind, start, \"end\", text "
                             "FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        if not r:
            raise ValueError(f"no chunk {chunk_id!r} (the index may have been rebuilt; search again)")
        return dict(r)

    def list_sections(self, key: str) -> list[dict]:
        it = self._item_ok(key)
        return [dict(r) for r in self.con.execute(
            "SELECT slug, title, level, kind, \"end\" - start AS chars FROM sections WHERE key=? ORDER BY ord",
            (it.key,))]

    def get_section(self, key: str, section: str, offset: int = 0) -> dict:
        it = self._item_ok(key, need_text=True)
        got = index.section_text(self.con, it.key, section, self.lib)
        if not got:
            raise ValueError(f"{it.key} has no section {section!r}; see list_sections")
        meta, text = got
        part = text[offset:offset + MAX_SECTION_CHARS]
        nxt = offset + MAX_SECTION_CHARS if offset + MAX_SECTION_CHARS < len(text) else None
        return {"key": it.key, "section": section, "title": meta["title"], "text": part,
                "offset": offset, "next_offset": nxt, "length": len(text)}

    def get_paper(self, key: str) -> dict:
        it = self._item_ok(key)
        d = it.as_dict(self.purpose)
        r = self.con.execute("SELECT abstract, n_chunks, sections_detected FROM items WHERE key=?",
                             (it.key,)).fetchone()
        if r:
            d.update(abstract=r["abstract"], n_chunks=r["n_chunks"])
        return d

    def related_papers(self, key: str, direction: str = "both", k: int = 20) -> dict:
        it = self._item_ok(key)
        out = {}
        if direction in ("cites", "both"):
            out["cites"] = [r[0] for r in self.con.execute("SELECT dst FROM edges WHERE src=?", (it.key,))]
        if direction in ("cited_by", "both"):
            out["cited_by"] = [r[0] for r in self.con.execute("SELECT src FROM edges WHERE dst=?", (it.key,))]
        if direction in ("similar", "both") and self.embedder is not None:
            out["similar"] = [s["key"] for s in search.similar(
                self.con, it.key, self.embedder.name, k=k, purpose=self.purpose)]
        for kk, v in out.items():
            out[kk] = [x for x in v if self.lib.get(x).allowed(self.purpose)][:k]
        return out

    def list_papers(self, author: str | None = None, year_from: int | None = None,
                    year_to: int | None = None, title_words: str | None = None,
                    tag: str | None = None, k: int = 25) -> list[dict]:
        w, a = [], []
        if author:
            w.append("authors LIKE ?"); a.append(f"%{author}%")
        if year_from:
            w.append("year >= ?"); a.append(year_from)
        if year_to:
            w.append("year <= ?"); a.append(year_to)
        if tag:
            w.append("(',' || tags || ',') LIKE ?"); a.append(f"%,{tag},%")
        for t in (title_words or "").split():
            w.append("title LIKE ?"); a.append(f"%{t}%")
        lv = [l for l in S.LEVELS if S.allow(l, self.purpose)]
        w.append(f"sharing IN ({','.join('?' * len(lv))})"); a += lv
        rows = self.con.execute(f"SELECT key, title, year, authors FROM items WHERE {' AND '.join(w)} "
                                f"ORDER BY year DESC LIMIT ?", a + [min(int(k), 100)])
        return [dict(r) for r in rows]

    def reading_due(self) -> dict:
        from . import reading
        d = reading.due(self.lib)
        return {"recall": [{"id": c["id"], "prompt": c.get("prompt", ""), "box": c.get("box")}
                           for c in d["recall"]],
                "reading": [r["key"] for r in d["reading"]]}

    def get_note(self, key: str) -> dict:
        if self.purpose in (S.SERVE_META, S.SERVE_TEXT):
            raise PermissionError("notes are not served to others")
        it = self._item_ok(key)
        p = self.lib.root / "notes" / f"{it.key}.md"
        bl = refs.backlinks(self.lib).get(it.key, [])
        return {"key": it.key, "note": p.read_text("utf-8") if p.is_file() else "", "cited_in": bl}


TOOL_NAMES = ("search_literature", "get_chunk", "list_sections", "get_section", "get_paper",
              "related_papers", "list_papers", "reading_due", "get_note")
