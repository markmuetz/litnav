"""Convert a litman data directory (the system litnav replaces):

    <src>/literature/<key>/{ref.bib, tags.txt, summary.json, extracted_text.txt, <key>.pdf}
    <src>/projects/<name>/<key>   (symlinks)

into a litnav library. The source is only read. Keys are kept as they are,
even where they do not match the convention (`litnav check` lists those).

`tags.txt` is one comma-separated line. The tag `confidential` sets the
sharing level; `ocr_text` records the text source; `physical_copy` marks an
item with no PDF. Relations can be given as a TSV (key, relation, target).
"""
from __future__ import annotations

import csv
import json
import shutil
from collections import Counter
from pathlib import Path

from .. import projects
from ..meta import RELATIONS
from ..util import atomic_write, sha256_file


def read_tags(p: Path) -> list[str]:
    if not p.is_file():
        return []
    raw = p.read_text("utf-8", "replace").replace("\n", ",")
    return list(dict.fromkeys(t.strip() for t in raw.split(",") if t.strip()))


def convert(lib, src: Path, *, relations: Path | None = None, hash_pdfs: bool = True,
            apply: bool = False, log=print) -> Counter:
    src = Path(src).expanduser()
    lit = src / "literature"
    if not lit.is_dir():
        raise ValueError(f"{src} has no literature/ directory")
    c = Counter()
    rel_rows = []
    if relations:
        with open(relations, encoding="utf-8") as f:
            rel_rows = [r for r in csv.DictReader(f, delimiter="\t")]
    for d in sorted(p for p in lit.iterdir() if p.is_dir()):
        key = d.name
        c["items"] += 1
        if lib.exists(key):
            c["already in library"] += 1
            continue
        tags = read_tags(d / "tags.txt")
        conf = "confidential" in tags
        bibf = d / "ref.bib"
        old_pdf = next((p for p in d.glob("*.pdf")), None)
        pdf_src = old_pdf.resolve() if old_pdf and old_pdf.exists() else None
        if old_pdf and not old_pdf.exists():
            c["broken pdf link"] += 1
        c["with ref.bib"] += bibf.is_file()
        c["with summary"] += (d / "summary.json").is_file()
        c["with text"] += (d / "extracted_text.txt").is_file() and not conf
        c["with pdf"] += bool(pdf_src)
        c["confidential"] += conf
        if not apply:
            continue
        it = lib.create_item(key, bibf.read_text("utf-8", "replace") if bibf.is_file() else None,
                             tags=tags, sharing_level="confidential" if conf else None)
        m = it.meta
        files = m.setdefault("files", {})
        if pdf_src:
            # a PDF already inside pdf_dir under another name/folder is used where
            # it is, never copied (copying would duplicate it in a synced folder)
            try:
                rel = pdf_src.relative_to(lib.pdf_dir.resolve())
            except ValueError:
                rel = None
            if rel is not None and rel.as_posix() != f"{key}.pdf":
                files["pdf_path"] = rel.as_posix()
                c["pdf used in place"] += 1
            dest = it.pdf_path
            if not dest.exists():
                lib.pdf_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(pdf_src, dest)
                c["pdf copied"] += 1
            if hash_pdfs:
                files["pdf_sha256"] = sha256_file(dest)
        elif "physical_copy" in tags or not old_pdf:
            files["pdf"] = False
        if (d / "summary.json").is_file():
            s = json.loads((d / "summary.json").read_text("utf-8"))
            s.setdefault("provenance", {"source": "legacy litman summary"})
            atomic_write(it.summary_path, json.dumps(s, indent=2, ensure_ascii=False) + "\n")
        if (d / "extracted_text.txt").is_file() and not conf:
            atomic_write(it.text_path, (d / "extracted_text.txt").read_text("utf-8", "replace"))
            files["text_source"] = "ocr:legacy" if "ocr_text" in tags else "pdftotext"
        it.save_meta()
    if apply:
        lib.invalidate()
        for r in rel_rows:
            if r.get("relation") not in RELATIONS or not lib.exists(r.get("key", "")):
                c["relation skipped"] += 1
                continue
            it = lib.get(r["key"])
            it.meta.setdefault("relations", {})[r["relation"]] = r.get("target", "")
            it.save_meta()
            c["relations"] += 1
        pdir = src / "projects"
        if pdir.is_dir():
            for proj in sorted(p for p in pdir.iterdir() if p.is_dir()):
                members = sorted(x.name for x in proj.iterdir() if lib.exists(x.name))
                if members:
                    projects.save(lib, proj.name, [{"key": k} for k in members])
                    c["projects"] += 1
    for k, v in c.items():
        log(f"  {v:6d}  {k}")
    return c
