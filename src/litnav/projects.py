"""Projects: projects/<name>/papers.toml, one [[paper]] table per member.

    [[paper]]
    key = "smith2021cold"
    status = "to read"          # reading depths, as in the reading list
    tags = ["mechanism"]         # project tags, kept out of the global tags
    added = 2026-11-15
    note = ""
"""
from __future__ import annotations

import tomllib

import tomli_w

from .util import atomic_write


def path(lib, name):
    return lib.root / "projects" / name / "papers.toml"


def load(lib, name) -> list[dict]:
    p = path(lib, name)
    if not p.is_file():
        raise ValueError(f"no project {name!r} (expected {p})")
    with open(p, "rb") as fh:
        return tomllib.load(fh).get("paper", [])


def save(lib, name, papers: list[dict]) -> None:
    atomic_write(path(lib, name), tomli_w.dumps({"paper": papers}))


def names(lib) -> list[str]:
    d = lib.root / "projects"
    return sorted(p.parent.name for p in d.glob("*/papers.toml")) if d.is_dir() else []


def project_keys(lib, name) -> list[str]:
    return [p["key"] for p in load(lib, name)]


def _write(lib, name, papers):
    path(lib, name).parent.mkdir(parents=True, exist_ok=True)
    save(lib, name, sorted(papers, key=lambda p: p["key"]))


def add(lib, name, keys_, *, status=None, tags=(), note=None, create=False) -> list[str]:
    """Add papers (keys resolved, so aliases work). Returns the keys added;
    a paper already in the project gets the status, tags and note given."""
    import datetime as dt
    if not path(lib, name).is_file() and not create:
        raise ValueError(f"no project {name!r}; --create to start one")
    papers = load(lib, name) if path(lib, name).is_file() else []
    by = {p["key"]: p for p in papers}
    added = []
    for k in keys_:
        key = lib.get(k).key
        p = by.get(key)
        if p is None:
            p = by[key] = {"key": key, "added": dt.date.today()}
            papers.append(p)
            added.append(key)
        _set(p, status, tags, note)
    _write(lib, name, papers)
    return added


def _set(p, status=None, tags=(), note=None, untags=()):
    if status is not None:
        if status:
            p["status"] = status
        else:
            p.pop("status", None)
    t = [x for x in p.get("tags", []) if x not in untags]
    t += [x for x in tags if x not in t]
    if t:
        p["tags"] = t
    else:
        p.pop("tags", None)
    if note is not None:
        if note:
            p["note"] = note
        else:
            p.pop("note", None)


def update(lib, name, keys_, *, status=None, tags=(), untags=(), note=None) -> list[str]:
    papers = load(lib, name)
    by = {p["key"]: p for p in papers}
    done = []
    for k in keys_:
        key = lib.resolve(k) or k
        if key not in by:
            raise ValueError(f"{key} is not in project {name}")
        _set(by[key], status, tags, note, untags)
        done.append(key)
    _write(lib, name, papers)
    return done


def remove(lib, name, keys_) -> list[str]:
    papers = load(lib, name)
    want = {lib.resolve(k) or k for k in keys_}
    gone = [p["key"] for p in papers if p["key"] in want]
    _write(lib, name, [p for p in papers if p["key"] not in want])
    return gone
