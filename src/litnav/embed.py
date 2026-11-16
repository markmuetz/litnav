"""Local embeddings (fastembed, ONNX; no PyTorch). Optional: `litnav[search]`.

Vectors live in the index's `vectors` table as float32 BLOBs keyed by a hash
of (model, embedded text), so only changed text is ever re-embedded, and a
run can be interrupted and resumed. Chunks are embedded as header + text;
papers as title + summary (or title + abstract + first chunk without one).
"""
from __future__ import annotations

import hashlib
import os
import time

from . import sharing as SH

BATCH = 64


def available() -> bool:
    try:
        import fastembed  # noqa: F401
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


def vhash(model: str, text: str) -> str:
    return hashlib.sha1(f"{model}\n{text}".encode()).hexdigest()


class Embedder:
    """Wraps the model. Tests substitute a fake with the same two methods."""

    def __init__(self, model: str, threads: int | None = None):
        if not available():
            raise RuntimeError("semantic search needs `pip install 'litnav[search]'` "
                               "(fastembed, numpy)")
        from fastembed import TextEmbedding
        cache = os.environ.get("LITNAV_MODEL_CACHE", os.path.expanduser("~/.cache/fastembed"))
        self.name = model
        self.model = TextEmbedding(model, cache_dir=cache, threads=threads)

    def docs(self, texts):
        import numpy as np
        return np.asarray(list(self.model.passage_embed(texts, batch_size=BATCH)), dtype="float32")

    def query(self, text):
        import numpy as np
        return np.asarray(list(self.model.query_embed(text))[0], dtype="float32")


def chunk_text(row) -> str:
    return f"{row['header']}\n{row['text']}"


def paper_text(con, key: str) -> str:
    it = con.execute("SELECT title, topic, summary, abstract FROM items WHERE key=?", (key,)).fetchone()
    if it is None:
        return ""
    if it["summary"]:
        body = f"{it['topic']} {it['summary']}"
    else:
        first = con.execute("SELECT text FROM chunks WHERE key=? AND kind!='caption' "
                            "ORDER BY ord LIMIT 1", (key,)).fetchone()
        body = f"{it['abstract']} {first['text'] if first else ''}"
    return f"{it['title']}\n{body}".strip()


def update(lib, con, embedder, *, limit: int | None = None, log=print) -> dict:
    """Embed every chunk and paper whose text has no vector yet."""
    import numpy as np
    model = embedder.name
    stats = {"chunks": 0, "papers": 0}
    # chunks of items whose sharing allows nothing beyond local are still
    # embedded locally: embedding is local computation, nothing leaves.
    rows = con.execute("SELECT id, key, header, text, vhash FROM chunks").fetchall()
    have = {r[0] for r in con.execute("SELECT vhash FROM vectors WHERE model=?", (model,))}
    todo = []
    for r in rows:
        h = vhash(model, chunk_text(r))
        if r["vhash"] != h:
            con.execute("UPDATE chunks SET vhash=? WHERE id=?", (h, r["id"]))
        if h not in have:
            todo.append((h, chunk_text(r)))
            have.add(h)
    for k in [r[0] for r in con.execute("SELECT key FROM items")]:
        t = paper_text(con, k)
        if not t:
            continue
        h = vhash(model, t)
        con.execute("INSERT OR REPLACE INTO paper_vectors VALUES (?,?)", (k, h))
        if h not in have:
            todo.append((h, t))
            have.add(h)
            stats["papers"] += 1
    con.commit()
    if limit:
        todo = todo[:limit]
    t0, done = time.time(), 0
    for i in range(0, len(todo), BATCH * 4):
        batch = todo[i:i + BATCH * 4]
        vecs = embedder.docs([t for _, t in batch])
        con.executemany("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)",
                        [(h, model, vecs.shape[1], np.ascontiguousarray(v).tobytes())
                         for (h, _), v in zip(batch, vecs)])
        con.commit()                      # resumable: every batch is durable
        done += len(batch)
        rate = done / max(1e-6, time.time() - t0)
        log(f"  embedded {done}/{len(todo)}  ({rate:.0f}/s, ~{(len(todo) - done) / max(rate, 1e-6) / 60:.0f} min left)")
    stats["chunks"] = len(todo) - stats["papers"]
    from .index import bump
    bump(con)
    con.commit()
    return stats


_CACHE: dict = {}


def matrix(con, model: str, what: str = "chunks"):
    """(ids, keys, sharing, normalised matrix) for chunks or papers, cached
    per connection until the vectors table changes."""
    import numpy as np
    from .index import generation
    path = con.execute("PRAGMA database_list").fetchone()[2]
    try:
        ino = os.stat(path).st_ino
    except OSError:
        ino = None
    n = con.execute("SELECT count(*) FROM vectors WHERE model=?", (model,)).fetchone()[0]
    ck, fp = (path, model, what), (ino, generation(con), n)
    hit = _CACHE.get(ck)
    if hit and hit[0] == fp:
        return hit[1]
    if what == "chunks":
        q = ("SELECT c.id, c.key, c.sharing, v.vec FROM chunks c JOIN vectors v "
             "ON v.vhash=c.vhash AND v.model=?")
    else:
        q = ("SELECT p.key, p.key, i.sharing, v.vec FROM paper_vectors p JOIN vectors v "
             "ON v.vhash=p.vhash AND v.model=? JOIN items i ON i.key=p.key")
    rows = con.execute(q, (model,)).fetchall()
    if not rows:
        res = ([], [], [], np.zeros((0, 1), dtype="float32"))
    else:
        m = np.frombuffer(b"".join(r[3] for r in rows), dtype="float32").reshape(len(rows), -1)
        m = m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
        res = ([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], m)
    _CACHE[ck] = (fp, res)          # one entry per index file, model and kind
    return res


def allowed_mask(sharing_levels, purpose):
    import numpy as np
    return np.array([SH.allow(s, purpose) for s in sharing_levels], dtype=bool)
