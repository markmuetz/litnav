"""Import from a .bib file with `file` fields: Zotero (Better BibTeX),
JabRef, Mendeley. The entry's own key is kept as an alias when litnav derives
a different one, so documents that cite the old key still resolve."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .. import bib, keys
from ..importer import Catalogue, norm_title  # noqa: F401
from ..identify import Proposal
from ..util import sha256_file


def file_paths(field: str) -> list[str]:
    """`desc:path:mime;desc:path:mime` (Zotero/JabRef/Mendeley), with `\\:`
    and `\\\\` escapes (Windows drive letters), or a bare path."""
    out = []
    for part in re.split(r"(?<!\\);", field):
        part = part.strip()
        if not part:
            continue
        bits = re.split(r"(?<!\\):", part)
        if len(bits) >= 3:
            path = ":".join(bits[1:-1])
        elif len(bits) == 2:
            path = bits[1] if bits[0] == "" or "/" not in bits[0] else part
        else:
            path = bits[0]
        path = path.replace("\\:", ":").replace("\\\\", "\\")
        out.append(path)
    return out


def pdf_for(entry: bib.Entry, base: Path | None) -> Path | None:
    f = entry.get("file")
    if not f:
        return None
    for p in file_paths(f):
        if p.lower().endswith(".pdf"):
            q = Path(p).expanduser()
            if not q.is_absolute() and base:
                q = base / q
            if q.is_file():
                return q
    return None


SAFE = re.compile(r"^[A-Za-z0-9_.+-]+$")


def plan(lib, bibfile: Path, pdf_root: Path | None = None, log=print) -> list[dict]:
    style = lib.config["keys"].get("style", "derive")
    extra_stop = lib.config["keys"].get("extra_stop_words", [])
    cat = Catalogue(lib)
    rows = []
    entries = bib.parse_many(Path(bibfile).read_text("utf-8", "replace"))
    base = pdf_root or Path(bibfile).parent
    for e in entries:
        title, year = keys.plain(e.get("title")), e.get("year")
        sur = keys.surname(e.get("author") or e.get("editor"))
        if style == "keep" and SAFE.match(e.key) and not cat.exists(e.key):
            key = e.key
        else:
            key = keys.derive_unique(sur, year, title, cat.exists, extra_stop=extra_stop) or ""
        pdf = pdf_for(e, base)
        sha = sha256_file(pdf) if pdf else ""
        p = Proposal(file=pdf.name if pdf else "", key=key, title=title, doi=bib.doi_of(e),
                     via="bib", year=int(year) if keys.plausible_year(year) else None,
                     extra={"author": (e.authors() or [""])[0]})
        status = "ok" if key else "no-key"
        dup = cat.duplicate(p, sha) if key else None
        if dup:
            status = "held"
        elif key:
            cat.add(p, sha)
        clean = bib.Entry(e.type, key or e.key, [(n, v) for n, v in e.fields if n not in
                                                 ("file", "abstract", "keywords", "mendeley-tags")])
        rows.append({"key": key, "status": status, "orig_key": e.key, "path": str(pdf or ""),
                     "sha256": sha, "dup_of": dup or "", "bibtex": bib.format_entry(clean),
                     "title": title, "keywords": e.get("keywords")})
        log(f"  {e.key[:36]:<38} {key or '-':<34} {status}" + (f" (= {dup})" if dup else ""))
    return rows


def apply(lib, rows, *, tags=(), keep_keywords_as_tags=False, log=print) -> dict:
    c = {"imported": 0, "held": 0, "left": 0}
    for r in rows:
        if r["status"] != "ok":
            c["held" if r["status"] == "held" else "left"] += 1
            continue
        t = list(tags)
        if keep_keywords_as_tags and r.get("keywords"):
            t += [re.sub(r"\s+", "_", k.strip()) for k in re.split(r"[,;]", r["keywords"]) if k.strip()]
        it = lib.create_item(r["key"], r["bibtex"], tags=t)
        if r["orig_key"] != r["key"]:
            it.meta["aliases"] = [r["orig_key"]]
        if r["path"]:
            lib.pdf_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(r["path"], it.pdf_path)
            it.meta.setdefault("files", {})["pdf_sha256"] = r["sha256"]
        else:
            it.meta.setdefault("files", {})["pdf"] = False
        it.save_meta()
        lib.invalidate()
        if r["path"]:
            from ..extract import extract_item      # as `litnav import` does; never for confidential
            try:
                extract_item(lib.get(r["key"]), log=log)
            except Exception as e:                  # a broken PDF must not stop the import
                log(f"  {r['key']}: no text ({e}); try `litnav extract {r['key']}`")
        c["imported"] += 1
    log(f"imported {c['imported']}, already held {c['held']}, not imported {c['left']}")
    return c
