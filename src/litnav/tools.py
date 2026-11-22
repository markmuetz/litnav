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
    def __init__(self, lib, purpose: str = S.SEND_OUT, embedder=None, access_log=None):
        self.lib, self.purpose = lib, lib.purpose(purpose)
        # keys whose text was handed out (a local agent's run: litnav ask-local
        # scrubs its answer against these papers)
        self.access_log = access_log
        # Metadata (title, authors, keys, section titles) still goes to the model
        # when the owner said no to licensed text: it is what lookups send anyway.
        self.meta_purpose = S.LOOKUP if self.purpose == S.SEND_OUT_STRICT else self.purpose
        self._reader = index.Reader(lib)
        self._reader()                       # fail now if there is no index yet
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

    @property
    def con(self):
        return self._reader()

    def _live(self, rows, purpose, key="key"):
        """Drop rows whose item's *current* sharing (meta.toml) forbids the
        purpose. The index keeps a copy of each item's level for fast
        filtering; this catches a level changed since the last `litnav index`."""
        ok: dict[str, bool] = {}
        out = []
        for r in rows:
            k = r[key]
            if k not in ok:
                kk = self.lib.resolve(k)
                ok[k] = bool(kk) and self.lib.get(kk).allowed(purpose)
            if ok[k]:
                out.append(r)
        return out

    def _item_ok(self, key: str, need_text: bool = False):
        k = self.lib.resolve(key)
        if not k:
            raise ValueError(f"no paper {key!r}")
        it = self.lib.get(k)
        need = self.purpose if need_text else self.meta_purpose
        if self.purpose in (S.SERVE_META, S.SERVE_TEXT) and need_text:
            need = S.SERVE_TEXT
        if not it.allowed(need):
            raise PermissionError(f"{k} is not available for this use ({it.sharing})")
        if need_text:
            self._accessed([it.key])
        return it

    def _accessed(self, keys):
        if self.access_log and keys:
            with open(self.access_log, "a", encoding="utf-8") as f:
                f.write("".join(f"{k}\n" for k in dict.fromkeys(keys)))

    # -- tools

    def search_literature(self, query: str, k: int = 10, mode: str = "hybrid", papers: bool = False,
                          year_from: int | None = None, year_to: int | None = None,
                          tag: str | None = None, author: str | None = None,
                          project: str | None = None) -> list[dict]:
        k = max(1, min(int(k), MAX_K))
        f = search.Filters(year_from=year_from, year_to=year_to, tag=tag, author=author,
                           keys=self._project_keys(project))
        purpose = S.SERVE_TEXT if self.purpose == S.SERVE_META and not papers else self.purpose
        # Strict policy: search licensed papers too, but return where, not what,
        # so the agent knows a paper matched and can ask for a verdict.
        strict = self.purpose == S.SEND_OUT_STRICT
        sp = self.meta_purpose if strict else purpose
        if papers:
            res = self._live(search.papers(self.con, query, embedder=self.embedder, k=k, filters=f,
                                           purpose=sp), sp)
            return [self._withheld(r, "best_passage") for r in res] if strict else res
        res = self._live(search.search(self.con, query, embedder=self.embedder, mode=mode, k=k,
                                       filters=f, purpose=sp), sp)
        keep = ("id", "key", "title", "year", "authors", "section_title", "kind", "start", "end",
                "text", "score")
        out = [{x: r.get(x) for x in keep} for r in res]
        if strict:
            return [self._withheld(r, "text") for r in out]
        self._accessed([r["key"] for r in out])
        return out

    WITHHELD = ("[withheld: this paper's text may not go to a model. verify_claim checks a claim "
                "against it with the user's local model]")

    def _withheld(self, r: dict, field: str) -> dict:
        k = self.lib.resolve(r["key"])
        if k and self.lib.get(k).allowed(self.purpose):
            return r
        r = dict(r)
        if field == "text":
            r["text"], r["text_withheld"] = self.WITHHELD, True
        elif r.get(field):
            r[field] = dict(r[field], text=self.WITHHELD)
        return r

    def _profile(self, profile):
        import contextlib

        @contextlib.contextmanager
        def use():
            old, self.lib.model_profile = self.lib.model_profile, profile or self.lib.model_profile
            try:
                yield
            finally:
                self.lib.model_profile = old
        return use()

    def ask_local_model(self, question: str, keys: list[str] | None = None,
                        profile: str | None = None) -> dict:
        """Ask the user's local model a question that needs papers whose text may
        not go to you. It reads the library and answers in its own words with
        [@key] citations; long verbatim passages are cut before you see it."""
        from . import asklocal
        if self.purpose in (S.SERVE_META, S.SERVE_TEXT):
            raise PermissionError("ask_local_model is for the library's owner")
        with self._profile(profile):
            rec = asklocal.ask(self.lib, question, keys=keys)
        return {k: rec[k] for k in ("answer", "cited", "read", "model", "engine", "verbatim_removed")}

    def verify_claim(self, key: str, claim: str, profile: str | None = None) -> dict:
        with self._profile(profile):
            return self._verify_claim(key, claim)

    def _verify_claim(self, key: str, claim: str) -> dict:
        """Check a claim against one paper with the user's local model. Returns
        a verdict, the location (section, page) and confidence, not the
        paper's text. With no local model running, the check is queued for
        the user's next batch run."""
        from . import localmodel
        if self.purpose in (S.SERVE_META, S.SERVE_TEXT):
            raise PermissionError("verify_claim is for the library's owner")
        it = self._item_ok(key)
        job = localmodel.verify_job(self.lib, it.key, claim)
        try:
            localmodel.endpoint(self.lib)
            st = localmodel.run_now(self.lib, [job], log=lambda m: None)[0]
        except ValueError:
            st = {"status": "no local model"}
        if st["status"] == "ok":
            return localmodel.for_agent(self.lib, st["record"])
        localmodel.enqueue(self.lib, [job])
        return {"key": it.key, "claim": claim, "verdict": "queued",
                "note": f"not checked now ({st.get('error') or st['status']}); queued for the "
                        "user's next batch run (`litnav queue export`)."}

    def _project_keys(self, project):
        if not project:
            return None
        from . import projects
        return projects.project_keys(self.lib, project)

    def search_notes(self, query: str, k: int = 10, kind: str | None = None) -> list[dict]:
        """The user's own notes, concepts and topic notes. Each note's level is
        re-derived from the file before it is returned (it may cite a paper
        made confidential, or the owner changed `[notes] to_models`, since the
        last `litnav index`)."""
        if self.purpose in (S.SERVE_META, S.SERVE_TEXT):
            raise PermissionError("notes are not served to others")
        import sqlite3
        from . import notes
        k = max(1, min(int(k), MAX_K))
        kinds = (kind,) if kind else ("note", "concept", "topic")
        try:
            # all levels from the index, then each note's *current* level
            # decides: a changed `[notes] to_models` or a newly confidential
            # paper applies without waiting for `litnav index`
            res = search.notes(self.con, query, embedder=self.embedder, k=k * 4,
                               purpose=S.LOCAL, kinds=kinds)
        except sqlite3.OperationalError:
            raise ValueError("notes are not indexed yet: run `litnav index`")
        lv: dict[str, bool] = {}
        out = []
        for r in res:
            if r["path"] not in lv:
                lv[r["path"]] = S.allow(notes.level(self.lib, r["path"]), self.purpose)
            if lv[r["path"]]:
                out.append({x: r[x] for x in ("id", "path", "kind", "about", "title", "section",
                                              "text", "score")})
        return out[:k]

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
        if r and it.allowed(self.purpose):
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
                self.con, it.key, self.embedder.name, k=k, purpose=self.meta_purpose)]
        for kk, v in out.items():
            out[kk] = [x["key"] for x in self._live([{"key": x} for x in v], self.meta_purpose)][:k]
        return out

    def list_papers(self, author: str | None = None, year_from: int | None = None,
                    year_to: int | None = None, title_words: str | None = None,
                    tag: str | None = None, project: str | None = None, k: int = 25) -> list[dict]:
        w, a = [], []
        pk = self._project_keys(project)
        if pk is not None:
            w.append(f"key IN ({','.join('?' * len(pk)) or 'NULL'})"); a += pk
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
        lv = [l for l in S.LEVELS if S.allow(l, self.meta_purpose)]
        w.append(f"sharing IN ({','.join('?' * len(lv))})"); a += lv
        rows = self.con.execute(f"SELECT key, title, year, authors FROM items WHERE {' AND '.join(w)} "
                                f"ORDER BY year DESC LIMIT ?", a + [min(int(k), 100)])
        return self._live([dict(r) for r in rows], self.meta_purpose)

    def reading_due(self) -> dict:
        from . import reading
        d = reading.due(self.lib)

        def ok(ident):                       # concept cards are the user's own
            if ident.startswith("concept:"):
                return True
            k = self.lib.resolve(ident)
            return bool(k) and self.lib.get(k).allowed(self.meta_purpose)
        return {"recall": [{"id": c["id"], "prompt": c.get("prompt", ""), "box": c.get("box")}
                           for c in d["recall"] if ok(c["id"])],
                "reading": [r["key"] for r in d["reading"] if ok(r["key"])]}

    def get_note(self, key: str) -> dict:
        if self.purpose in (S.SERVE_META, S.SERVE_TEXT):
            raise PermissionError("notes are not served to others")
        it = self._item_ok(key)
        p = self.lib.root / "notes" / f"{it.key}.md"
        bl = refs.backlinks(self.lib).get(it.key, [])
        return {"key": it.key, "note": p.read_text("utf-8") if p.is_file() else "", "cited_in": bl}


TOOL_NAMES = ("search_literature", "search_notes", "get_chunk", "list_sections", "get_section", "get_paper",
              "related_papers", "list_papers", "reading_due", "get_note", "verify_claim",
              "ask_local_model")
