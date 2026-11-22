"""Find which papers are openly licensed, and mark them `open`.

A paper under a Creative Commons licence that allows redistribution may go to
any model; a paper held under a publisher or university licence may not
(when licensed_to_models = false). Crossref records the licence each
publisher deposits, with the date it starts (an embargo ends then).

Only `licensed` items are ever changed, and only to `open`. Which CC
licences count as open is the owner's call, `[licences] open` in
library.toml; the default is the licences that allow redistribution with no
conditions beyond credit: CC BY, CC BY-SA, CC0 and public domain. NC and ND
licences are reported, not applied.

Every check is recorded in meta.toml ([licence]: url, source, checked), so a
paper is only looked up again with --recheck.
"""
from __future__ import annotations

import datetime as dt
import re

from . import sharing as S

DEFAULT_OPEN = ["by", "by-sa", "cc0", "publicdomain"]
CC = re.compile(r"creativecommons\.org/(licenses|publicdomain)/([a-z-]+)", re.I)


def code(url: str) -> str | None:
    """'by', 'by-nc-nd', 'cc0', 'publicdomain' ... or None for a non-CC URL."""
    m = CC.search(url or "")
    if not m:
        return None
    kind, name = m.group(1).lower(), m.group(2).lower()
    if kind == "publicdomain":
        return "cc0" if name == "zero" else "publicdomain"
    return name


def _start(lic: dict) -> dt.date | None:
    parts = (lic.get("start") or {}).get("date-parts") or [[]]
    try:
        y, m, d = (parts[0] + [1, 1])[:3]
        return dt.date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None


def best(msg: dict, today: dt.date | None = None) -> tuple[str, str] | None:
    """(code, url) of the CC licence on the version of record (or any version,
    if none is marked) that is in force today; None if there is none."""
    today = today or dt.date.today()
    lics = msg.get("license") or []
    vor = [l for l in lics if l.get("content-version") in ("vor", "unspecified")] or lics
    for l in vor:
        c = code(l.get("URL", ""))
        st = _start(l)
        if c and (st is None or st <= today):
            return c, l["URL"]
    return None


def open_codes(lib) -> set[str]:
    return set(lib.config.get("licences", {}).get("open", DEFAULT_OPEN))


def apply_to(it, found: tuple[str, str] | None, opens: set[str], *, apply: bool) -> str:
    """Record a licence check on an item; returns 'open', 'cc-other', 'none'."""
    lic = {"source": "crossref", "checked": dt.date.today().isoformat()}
    if found:
        lic["url"], lic["code"] = found[1], found[0]
    status = "none" if not found else ("open" if found[0] in opens else "cc-other")
    if apply:
        it.meta["licence"] = lic
        if status == "open" and it.sharing == "licensed":
            it.meta["sharing"] = "open"
        it.save_meta()
    return status


def run(lib, client, *, apply=False, recheck=False, keys=None, log=print) -> dict:
    from .sources import crossref
    opens = open_codes(lib)
    items = [lib.get(k) for k in keys] if keys else list(lib.items())
    counts = {"open": 0, "cc-other": 0, "none": 0, "no-doi": 0, "skipped": 0, "failed": 0}
    todo = []
    for it in items:
        if it.sharing != "licensed" or (it.meta.get("licence") and not recheck):
            counts["skipped"] += 1
        elif not it.doi:
            counts["no-doi"] += 1
        elif not it.allowed(S.LOOKUP):
            counts["skipped"] += 1
        else:
            todo.append(it)
    for i, it in enumerate(todo, 1):
        msg = crossref.work(client, it.doi, items=[it])
        if msg is None:
            counts["failed"] += 1
            continue
        found = best(msg)
        st = apply_to(it, found, opens, apply=apply)
        counts[st] += 1
        if st != "none":
            log(f"  [{i}/{len(todo)}] {it.key}: {found[0]}" + (" -> open" if st == "open" else
                                                              " (not in [licences] open)"))
    return counts
