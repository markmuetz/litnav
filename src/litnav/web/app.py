"""`litnav serve`: the localhost web app (design § 16a).

Security model (it serves full text and PDFs of licensed papers):
- binds to 127.0.0.1 only;
- every request's Host header must be 127.0.0.1:<port> or localhost:<port>
  (defeats DNS rebinding);
- GET / sets a random per-run session cookie (HttpOnly, SameSite=Strict);
  every /api and /pdf request must carry it;
- writes (POST) must also send the token in X-Litnav-Token and come from our
  own Origin; no CORS headers are ever sent;
- the page builds DOM with textContent and escapes before rendering
  Markdown, so library text cannot inject script.
Writes go through the Library API and are committed to the library's git
repo, one commit per write, touching only the files written.
"""
from __future__ import annotations

import json
import secrets
import subprocess
import threading
import webbrowser
from pathlib import Path

from .. import index, reading, refs, search
from .. import sharing as S
from ..library import Library

STATIC = Path(__file__).parent / "static"


class Git:
    """Commit only the paths a write touched; never during a merge/rebase."""

    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()
        self.enabled = (root / ".git").is_dir()

    def busy(self) -> bool:
        g = self.root / ".git"
        return any((g / x).exists() for x in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "index.lock"))

    def commit(self, paths: list[Path], message: str) -> str:
        if not self.enabled:
            return "no git"
        with self.lock:
            if self.busy():
                return "skipped: git is busy (merge, rebase or lock)"
            rel = [str(p.relative_to(self.root)) for p in paths if p.exists()]
            if not rel:
                return "nothing to commit"
            subprocess.run(["git", "add", "--", *rel], cwd=self.root, capture_output=True)
            r = subprocess.run(["git", "commit", "-q", "-m", message, "--", *rel], cwd=self.root,
                               capture_output=True, text=True)
            return "committed" if r.returncode == 0 else ("nothing to commit" if "nothing" in r.stdout + r.stderr
                                                          else r.stderr.strip()[:200])


def create_app(lib: Library, port: int, token: str | None = None):
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.requests import Request
    from starlette.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
                                     Response, StreamingResponse)
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles

    token = token or secrets.token_urlsafe(24)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}
    git = Git(lib.root)
    state = {"tools": None}

    con = index.Reader(lib)          # one connection, reopened after a rebuild

    class Guard:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            req = Request(scope)
            if req.headers.get("host") not in hosts:
                return await PlainTextResponse("bad host", 403)(scope, receive, send)
            path = scope["path"]
            if path.startswith(("/api", "/pdf")):
                if req.cookies.get("litnav") != token:
                    return await PlainTextResponse("no session: reload the page", 401)(scope, receive, send)
                if req.method != "GET":
                    if req.headers.get("x-litnav-token") != token or \
                            req.headers.get("origin") not in origins:
                        return await PlainTextResponse("forbidden", 403)(scope, receive, send)
            await self.app(scope, receive, send)

    async def home(request):
        html = (STATIC / "index.html").read_text("utf-8").replace("__TOKEN__", token)
        r = HTMLResponse(html, headers={"Cache-Control": "no-store",
                                        "Content-Security-Policy":
                                            "default-src 'self'; img-src 'self' data:; "
                                            "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"})
        r.set_cookie("litnav", token, httponly=True, samesite="strict")
        return r

    async def items(request):
        q = request.query_params
        c = con()
        w, a = [], []
        if q.get("tag"):
            w.append("(',' || tags || ',') LIKE ?"); a.append(f"%,{q['tag']},%")
        if q.get("project"):
            from ..projects import project_keys
            ks = project_keys(lib, q["project"])
            w.append(f"key IN ({','.join('?' * len(ks)) or 'NULL'})"); a += ks
        if q.get("q"):
            for t in q["q"].split():
                w.append("(title LIKE ? OR authors LIKE ? OR key LIKE ?)"); a += [f"%{t}%"] * 3
        rows = c.execute("SELECT key, title, authors, first_author, year, journal, tags, sharing, has_pdf, "
                         "has_text, n_chunks FROM items" + (f" WHERE {' AND '.join(w)}" if w else "") +
                         " ORDER BY year DESC, key", a).fetchall()
        return JSONResponse([dict(r) for r in rows])

    async def item(request):
        key = lib.resolve(request.path_params["key"])
        if not key:
            return JSONResponse({"error": "not found"}, 404)
        it = lib.get(key)
        c = con()
        d = it.as_dict()
        r = c.execute("SELECT abstract FROM items WHERE key=?", (key,)).fetchone()
        d["abstract"] = r["abstract"] if r else ""
        d["sections"] = [dict(x) for x in c.execute(
            "SELECT slug, title, level, kind FROM sections WHERE key=? ORDER BY ord", (key,))]
        d["cites"] = [x[0] for x in c.execute("SELECT dst FROM edges WHERE src=?", (key,))]
        d["cited_by"] = [x[0] for x in c.execute("SELECT src FROM edges WHERE dst=?", (key,))]
        d["bibtex"] = it.bib_path.read_text("utf-8") if it.bib_path.is_file() else ""
        note = lib.root / "notes" / f"{key}.md"
        d["note"] = note.read_text("utf-8") if note.is_file() else ""
        d["backlinks"] = refs.backlinks(lib).get(key, [])
        d["meta"] = json.loads(json.dumps(it.meta, default=str))
        d["reading"] = next((json.loads(json.dumps(x, default=str)) for x in reading.load_list(lib)
                             if x["key"] == key), None)
        titles = {x[0]: x[1] for x in c.execute(
            f"SELECT key, title FROM items WHERE key IN ({','.join('?' * len(d['cites'] + d['cited_by'])) or 'NULL'})",
            d["cites"] + d["cited_by"])}
        d["titles"] = titles
        return JSONResponse(d)

    async def section(request):
        key = lib.resolve(request.path_params["key"])
        got = index.section_text(con(), key, request.path_params["slug"], lib) if key else None
        if not got:
            return JSONResponse({"error": "not found"}, 404)
        return JSONResponse({"title": got[0]["title"], "text": got[1]})

    async def do_search(request):
        q = request.query_params
        query = q.get("q", "").strip()
        if not query:
            return JSONResponse({"semantic": False, "results": []})
        from ..commands.searching import embedder_for
        emb = None if q.get("mode") == "keyword" else embedder_for(lib)
        f = search.Filters()
        if q.get("year"):
            lo, _, hi = q["year"].partition("-")
            f.year_from, f.year_to = int(lo), int(hi or lo)
        if q.get("tag"):
            f.tag = q["tag"]
        k = min(int(q.get("k", 20)), 50)
        if q.get("papers"):
            res = search.papers(con(), query, embedder=emb, k=k, filters=f)
        else:
            res = search.search(con(), query, embedder=emb, mode=q.get("mode", "hybrid"), k=k, filters=f)
        return JSONResponse({"semantic": emb is not None, "results": res})

    async def similar(request):
        key = lib.resolve(request.path_params["key"])
        res = search.similar(con(), key, lib.config["search"]["embedding_model"], k=15) if key else []
        return JSONResponse(res)

    async def graph(request):
        p = lib.index_dir / "graph.json"
        if not p.is_file():
            return JSONResponse({"nodes": [], "links": [], "candidates": [], "missing": True})
        return Response(p.read_bytes(), media_type="application/json")

    async def tags(request):
        from collections import Counter
        from ..projects import names
        c = Counter()
        for r in con().execute("SELECT tags FROM items"):
            c.update(t for t in (r[0] or "").split(",") if t)
        return JSONResponse({"tags": c.most_common(), "projects": names(lib)})

    async def get_reading(request):
        lst = json.loads(json.dumps(reading.load_list(lib), default=str))
        due = json.loads(json.dumps(reading.due(lib), default=str))
        cards = json.loads(json.dumps(reading.load_cards(lib), default=str))
        titles = {r[0]: r[1] for r in con().execute("SELECT key, title FROM items")}
        for x in lst:
            x["title"] = titles.get(x["key"], "")
        return JSONResponse({"list": lst, "due": due, "cards": cards})

    async def candidates(request):
        from .. import discovery
        q = request.query_params
        cs = discovery.candidates(lib, min_cites=int(q.get("min_cites", 5)))[:min(int(q.get("limit", 150)), 500)]
        return JSONResponse(cs)

    async def get_tofetch(request):
        from .. import discovery
        return JSONResponse(discovery.load(lib))

    async def post_tofetch(request):
        from .. import discovery
        b = await request.json()
        act, doi = b.get("action"), b.get("doi", "")
        if act in ("add", "add_skip"):
            row = {k: str(b.get(k) or "") for k in ("doi", "title", "first_author", "year", "source", "note")}
            if act == "add_skip":
                row["status"] = "skip"
            discovery.add(lib, [row])
            msg = f"To fetch: {'skip' if act == 'add_skip' else 'add'} {doi or row['title'][:40]}."
        elif act in ("skip", "opened"):
            discovery.set_status(lib, [doi], act)
            msg = f"To fetch: {act} {doi}."
        else:
            return JSONResponse({"error": "unknown action"}, 400)
        return JSONResponse({"ok": True, "git": git.commit([discovery.tofetch_path(lib)], msg)})

    async def pdf(request):
        key = lib.resolve(request.path_params["key"])
        if not key:
            return PlainTextResponse("not found", 404)
        p = lib.get(key).pdf_path
        if not p.is_file():
            return PlainTextResponse("no PDF", 404)
        return FileResponse(p, media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{key}.pdf"'})

    async def bibtex(request):
        keys_ = [k for k in request.query_params.get("keys", "").split(",") if k]
        out = []
        for k in keys_:
            it = lib.get(k)
            if it.bib_path.is_file():
                out.append(it.bib_path.read_text("utf-8").strip())
        return PlainTextResponse("\n\n".join(out) + "\n")

    # -- writes
    async def post_tags(request):
        key = lib.resolve(request.path_params["key"])
        body = await request.json()
        it = lib.get(key)
        for t in body.get("add", []):
            it.add_tag(t)
        for t in body.get("remove", []):
            it.remove_tag(t)
        index.update(lib, keys_=[key], log=lambda *a: None)
        res = git.commit([it.meta_path], f"Tag {key}: +{','.join(body.get('add', []))} "
                                         f"-{','.join(body.get('remove', []))}.")
        return JSONResponse({"tags": lib.get(key).tags, "git": res})

    async def post_sharing(request):
        key = lib.resolve(request.path_params["key"])
        level = (await request.json()).get("level")
        it = lib.get(key)
        it.set_sharing(level)
        index.update(lib, keys_=[key], log=lambda *a: None)
        return JSONResponse({"sharing": level, "git": git.commit([it.meta_path], f"Set {key} {level}.")})

    async def post_reading(request):
        b = await request.json()
        act = b.get("action")
        if act == "add":
            reading.add_to_list(lib, b["key"], b.get("depth", "read"))
            msg = f"Add {b['key']} to the reading list."
        elif act == "done":
            reading.mark_done(lib, b["key"], b.get("depth", "read"))
            msg = f"Finish {b['key']} ({b.get('depth', 'read')})."
        elif act == "rate":
            reading.rate(lib, b["id"], b["rating"])
            msg = f"Recall {b['id']}: {b['rating']}."
        else:
            return JSONResponse({"error": "unknown action"}, 400)
        res = git.commit([reading.list_path(lib), reading.recall_path(lib)], msg)
        return JSONResponse({"ok": True, "git": res})

    async def ask(request):
        from ..ask import stream_answer
        b = await request.json()
        question = (b.get("question") or "").strip()
        if not question:
            return JSONResponse({"error": "empty question"}, 400)

        def gen():
            for ev in stream_answer(lib, question, history=b.get("history") or []):
                yield f"data: {json.dumps(ev)}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})

    async def ask_info(request):
        from ..ask import backend_status
        return JSONResponse(backend_status(lib))

    routes = [
        Route("/", home),
        Route("/api/items", items),
        Route("/api/item/{key}", item),
        Route("/api/item/{key}/section/{slug}", section),
        Route("/api/item/{key}/similar", similar),
        Route("/api/item/{key}/tags", post_tags, methods=["POST"]),
        Route("/api/item/{key}/sharing", post_sharing, methods=["POST"]),
        Route("/api/search", do_search),
        Route("/api/graph", graph),
        Route("/api/tags", tags),
        Route("/api/reading", get_reading),
        Route("/api/reading", post_reading, methods=["POST"]),
        Route("/api/bib", bibtex),
        Route("/api/ask", ask, methods=["POST"]),
        Route("/api/ask", ask_info),
        Route("/api/candidates", candidates),
        Route("/api/to-fetch", get_tofetch),
        Route("/api/to-fetch", post_tofetch, methods=["POST"]),
        Route("/pdf/{key}", pdf),
        Mount("/static", StaticFiles(directory=STATIC)),
    ]

    async def on_error(request, exc):
        return JSONResponse({"error": str(exc)}, 400)

    from ..library import KeyError_
    return Starlette(routes=routes, middleware=[Middleware(Guard)],
                     exception_handlers={ValueError: on_error, KeyError_: on_error,
                                         PermissionError: on_error})


def serve(library=None, port: int = 8765, browser: bool = True):
    import uvicorn
    lib = Library.find(library)
    if not index.db_path(lib).exists():
        print("building the index first (keyword only; `litnav index` adds embeddings) ...")
        index.update(lib)
    app = create_app(lib, port)
    url = f"http://127.0.0.1:{port}/"
    print(f"litnav: {url}  (Ctrl-C to stop)")
    if browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
