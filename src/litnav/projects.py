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
