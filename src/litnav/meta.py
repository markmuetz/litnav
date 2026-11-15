"""meta.toml: litnav's own per-item fields (design § 3.2). Every field is
optional; an item with only a ref.bib is valid."""
from __future__ import annotations

import datetime as _dt
import tomllib
from pathlib import Path

import tomli_w

from .util import atomic_write

RELATIONS = ("preprint_of", "corrigendum_of", "draft_of", "supplement_of", "part_of")


def empty() -> dict:
    return {"aliases": [], "tags": [], "ids": {}, "relations": {}, "files": {}}


def read(path: Path) -> dict:
    m = empty()
    if path.is_file():
        with open(path, "rb") as fh:
            m.update(tomllib.load(fh))
    return m


def write(path: Path, m: dict) -> None:
    # drop empty tables/lists so files stay short and diffs small
    clean = {}
    for k, v in m.items():
        if isinstance(v, dict):
            v = {a: b for a, b in v.items() if b not in ("", None, [], {})}
        if v in ("", None, [], {}):
            continue
        clean[k] = v
    order = ["sharing", "tags", "aliases", "added", "ids", "relations", "files"]
    ordered = {k: clean[k] for k in order if k in clean}
    ordered.update({k: v for k, v in clean.items() if k not in ordered})
    # scalars before tables, as TOML requires
    scal = {k: v for k, v in ordered.items() if not isinstance(v, dict)}
    tabs = {k: v for k, v in ordered.items() if isinstance(v, dict)}
    atomic_write(path, tomli_w.dumps({**scal, **tabs}))


def today() -> _dt.date:
    return _dt.date.today()
