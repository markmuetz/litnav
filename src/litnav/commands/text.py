"""extract, index, embed, doctor."""
from __future__ import annotations

import shutil
import sys

from .. import extract
from ..library import Library
from ..util import atomic_write


def add(sub):
    p = sub.add_parser("extract", help="extract text from PDFs that have none (never for confidential items)")
    p.add_argument("keys", nargs="*")
    p.add_argument("--force", action="store_true", help="re-extract even if text exists (not OCR'd text)")
    p.add_argument("--no-ocr", action="store_true")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("index", help="update the search index (incremental)")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--no-embed", action="store_true", help="keyword index only")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("doctor", help="check optional tools and the index")
    p.set_defaults(func=cmd_doctor)


def extract_item(it, *, force=False, ocr=True, log=print) -> str:
    if it.sharing == "confidential":
        return "confidential"
    if not it.has_pdf:
        return "no pdf"
    if it.has_text and not force:
        return "has text"
    src = it.meta.get("files", {}).get("text_source", "")
    if it.has_text and src.startswith("ocr"):
        return "ocr text kept"          # expensive to redo, and may be hand-checked
    text = extract.pdf_text(it.pdf_path)
    how = "pdftotext" if shutil.which("pdftotext") else "pypdfium2"
    if len(extract.own_text(text).strip()) < extract.MIN_TEXT:
        if not (ocr and extract.ocr_available()):
            return "no text layer (OCR unavailable)"
        log(f"  OCR {it.key} ...")
        text, how = extract.ocr(it.pdf_path), "ocr:tesseract"
    atomic_write(it.text_path, extract.own_text(text))
    it.meta.setdefault("files", {})["text_source"] = how
    it.save_meta()
    return "extracted"


def cmd_extract(a):
    from collections import Counter
    lib = Library.find(a.library)
    items = [lib.get(k) for k in a.keys] if a.keys else list(lib.items())
    c = Counter()
    for it in items:
        r = extract_item(it, force=a.force, ocr=not a.no_ocr)
        c[r] += 1
        if r == "extracted":
            print(f"  {it.key}", file=sys.stderr)
    print("  " + "  ".join(f"{k}={v}" for k, v in c.most_common()), file=sys.stderr)


def cmd_index(a):
    from .. import embed, index
    lib = Library.find(a.library)
    log = lambda m: print(m, file=sys.stderr)
    s = index.update(lib, rebuild=a.rebuild, log=log)
    log(f"index: {s['indexed']} indexed ({s['chunks']} chunks), {s['unchanged']} unchanged, "
        f"{s['removed']} removed")
    if a.no_embed:
        return
    if not embed.available():
        log("embeddings skipped: install litnav[search] for semantic search")
        return
    model = lib.config["search"]["embedding_model"]
    log(f"embedding with {model} (first run downloads the model) ...")
    e = embed.Embedder(model)
    con = index.connect(lib)
    try:
        r = embed.update(lib, con, e, log=log)
    finally:
        con.close()
    log(f"embedded {r['chunks']} chunks, {r['papers']} papers")


def cmd_doctor(a):
    from .. import embed, index
    b = extract.backends()
    ok = lambda x: "ok" if x else "missing"
    print(f"pdftotext      {ok(b['pdftotext'])}   (best PDF text; poppler)")
    print(f"pypdfium2      {ok(b['pypdfium2'])}   (fallback PDF text; litnav[pdf])")
    print(f"tesseract      {ok(b['tesseract'])}   (OCR of scans; with pdftoppm)")
    print(f"pdftoppm       {ok(b['pdftoppm'])}")
    print(f"git            {ok(shutil.which('git'))}")
    print(f"fastembed      {ok(embed.available())}   (semantic search; litnav[search])")
    try:
        lib = Library.find(a.library)
    except Exception as e:
        print(f"library        {e}")
        return
    print(f"library        {lib.root}")
    print(f"pdf_dir        {lib.pdf_dir} ({'ok' if lib.pdf_dir.is_dir() else 'MISSING'})")
    p = index.db_path(lib)
    if p.exists():
        con = index.connect(lib, readonly=True)
        n = con.execute("SELECT count(*), sum(n_chunks), sum(sections_detected), sum(has_text) FROM items").fetchone()
        v = con.execute("SELECT count(*) FROM vectors").fetchone()[0]
        c = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
        print(f"index          {n[0]} items, {c} chunks, {v} vectors; sections found in "
              f"{n[2]}/{n[3]} texts")
    else:
        print("index          none yet: run `litnav index`")
