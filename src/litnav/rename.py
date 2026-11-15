"""Rename an item's key, keeping the old key as an alias.

Moves: papers/<old>/ -> papers/<new>/, <pdf_dir>/<old>.pdf -> <new>.pdf (not
a PDF kept elsewhere via files.pdf_path), _variants/<old>.* -> <new>.*.
Rewrites: the ref.bib key; [@old] in notes/concepts/topics/projects;
project membership; other items' relations pointing at old.
"""
from __future__ import annotations

from . import bib, keys, projects, refs
from .library import Item, KeyError_, _UNSAFE
from .util import atomic_write


def plan(lib, old: str, new: str) -> dict:
    it = lib.get(old)
    if _UNSAFE.search(new) or not new:
        raise KeyError_(f"{new!r} is not safe as a filename")
    other = lib.resolve(new)
    if other and other != it.key:
        raise KeyError_(f"{new!r} already exists ({other})")
    probs = keys.validate(new)
    p = {"old": it.key, "new": new, "warnings": [f"new key: {x}" for x in probs],
         "files": [], "notes": [], "projects": [], "relations": []}
    if not it.meta.get("files", {}).get("pdf_path") and it.pdf_path.exists():
        p["files"].append((it.pdf_path, lib.pdf_dir / f"{new}.pdf"))
    if lib.variants.is_dir():
        for v in lib.variants.glob(f"{it.key}.*"):
            p["files"].append((v, v.with_name(new + v.name[len(it.key):])))
    for f in refs.md_files(lib):
        t = f.read_text("utf-8", "replace")
        n = sum(1 for r in refs.find(t) if r.key.casefold() == it.key.casefold())
        if n:
            p["notes"].append((f, n))
    for name in projects.names(lib):
        if it.key in projects.project_keys(lib, name):
            p["projects"].append(name)
    for o in lib.items():
        if o.key != it.key and it.key in o.meta.get("relations", {}).values():
            p["relations"].append(o.key)
    return p


def apply(lib, p: dict) -> None:
    old, new = p["old"], p["new"]
    it = lib.get(old)
    for src, dst in p["files"]:
        if dst.exists():
            raise FileExistsError(dst)
    newdir = lib.papers_dir / new
    if old.casefold() == new.casefold():
        tmp = lib.papers_dir / f".{old}.renaming"
        it.dir.rename(tmp)
        tmp.rename(newdir)
    else:
        it.dir.rename(newdir)
    for src, dst in p["files"]:
        src.rename(dst)
    lib.invalidate()
    n = Item(lib, new)
    if n.entry:
        e = n.entry
        e.key = new
        atomic_write(n.bib_path, bib.format_entry(e))
    al = n.meta.setdefault("aliases", [])
    if old not in al and old.casefold() != new.casefold():
        al.append(old)
    n.meta["aliases"] = [a for a in al if a.casefold() != new.casefold()]
    n.save_meta()
    for f, _ in p["notes"]:
        t, _n = refs.rewrite(f.read_text("utf-8"), old, new)
        atomic_write(f, t)
    for name in p["projects"]:
        rows = projects.load(lib, name)
        for r in rows:
            if r["key"] == old:
                r["key"] = new
        projects.save(lib, name, rows)
    lib.invalidate()
    for k in p["relations"]:
        o = lib.get(k)
        rel = o.meta.get("relations", {})
        for rk, rv in list(rel.items()):
            if rv == old:
                rel[rk] = new
        o.save_meta()
