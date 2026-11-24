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


def from_openalex(lic: str) -> str:
    """OpenAlex's licence names ('cc-by-nc-nd', 'public-domain') as codes here."""
    lic = (lic or "").lower()
    return "publicdomain" if lic == "public-domain" else lic.removeprefix("cc-")


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


# -- the licence line in the paper's own text (`litnav licences --from-text`)
# Runs locally; reports only a code and whether the line licenses the article
# itself, never the text around it.

_PHRASE = re.compile(r"creative\s*commons|creativecommons\.org|\bCC[\s-]?BY\b", re.I)
# words that say the licence is the article's own
_ARTICLE = re.compile(r"(open[\s-]access|this|the)\s+(article|paper|work|publication|"
                      r"manuscript|contribution)|distributed\s+under|licensed\s+under|"
                      r"published\s+under|terms\s+of\s+the", re.I)
# words that say it is about something else
_OTHER = re.compile(r"\b(data|dataset|datasets|code|software|images?|figure|map|photo|"
                    r"output|model output|repository|zenodo)\b", re.I)
_NAMED = [("by-nc-nd", r"attribution[\s-]*non[\s-]?commercial[\s-]*no[\s-]?deriv|by[\s-]nc[\s-]nd"),
          ("by-nc-sa", r"attribution[\s-]*non[\s-]?commercial[\s-]*share[\s-]?alike|by[\s-]nc[\s-]sa"),
          ("by-nc", r"attribution[\s-]*non[\s-]?commercial|by[\s-]nc\b"),
          ("by-nd", r"attribution[\s-]*no[\s-]?deriv|by[\s-]nd\b"),
          ("by-sa", r"attribution[\s-]*share[\s-]?alike|by[\s-]sa\b"),
          ("cc0", r"\bcc0\b|cc\s*zero|public\s+domain\s+dedication"),
          ("by", r"attribution|\bcc[\s-]?by\b")]


def from_text(text: str) -> tuple[str, str] | None:
    """(code, 'article' or 'mention') for the strongest CC statement in a
    paper's text, or None. 'article' means the wording licenses the paper
    itself; 'mention' is any other (data, code, a figure, a reference)."""
    t = " ".join(text.split())
    best_ = None
    for m in _PHRASE.finditer(t):
        win = t[max(0, m.start() - 250): m.end() + 250]
        url = CC.search(win)
        c = code(url.group(0)) if url else None
        if not c:
            for name, pat in _NAMED:
                if re.search(pat, win, re.I):
                    c = name
                    break
        if not c:
            continue
        near = t[max(0, m.start() - 160): m.end() + 60]
        kind = "article" if _ARTICLE.search(near) and not _OTHER.search(near) else "mention"
        if kind == "article":
            return c, kind
        best_ = best_ or (c, kind)
    return best_


def run_text(lib, *, apply=False, keys=None, log=print) -> dict:
    """Check licensed papers' own text for their CC licence line."""
    opens = open_codes(lib)
    items = [lib.get(k) for k in keys] if keys else list(lib.items())
    counts = {"open": 0, "cc-other": 0, "mention": 0, "none": 0, "no-text": 0}
    for it in items:
        if it.sharing != "licensed":
            continue
        if not it.has_text:
            counts["no-text"] += 1
            continue
        got = from_text(it.text())
        if not got:
            counts["none"] += 1
            continue
        c, kind = got
        if kind == "mention":
            counts["mention"] += 1
            log(f"  {it.key}: a CC {c} mention, not the article's licence (check by hand)")
            continue
        st = "open" if c in opens else "cc-other"
        counts[st] += 1
        log(f"  {it.key}: CC {c} on the article" + (" -> open" if st == "open" else
                                                     " (not in [licences] open)"))
        if apply:
            it.meta["licence"] = {"source": "text", "checked": dt.date.today().isoformat(),
                                  "code": c}
            if st == "open":
                it.meta["sharing"] = "open"
            it.save_meta()
    return counts
