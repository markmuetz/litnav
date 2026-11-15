"""Reading list and spaced recall (design § 10).

reading/list.toml
    [[paper]]
    key = "smith2021cold"
    depth = "read"              # deep, read, skim, scan, comment
    added = 2026-11-15
    status = "to read"          # to read, reading, done
    done = { depth = "skim", date = 2026-11-20 }
    note = ""

reading/recall.toml
    [[card]]
    id = "smith2021cold"        # a paper key, or concept:<slug>
    box = 2
    last_reviewed = 2026-11-10
    next_due = 2026-11-17
    prompt = "..."

Leitner boxes: 1 working day, 1 week, 1 month, 3 months. Ratings: easy
promotes one box (cap 4); medium stays, next due at the medium interval
(between hard and easy); hard stays; forgot goes back to box 1. Days in box 1
are working days; weeks and months are calendar time moved off weekends.
Only the user marks a paper read.
"""
from __future__ import annotations

import calendar
import datetime as dt
import tomllib

import tomli_w

from .util import atomic_write

DEPTHS = ("deep", "read", "skim", "scan", "comment")
RECALL_DEPTHS = ("deep", "read")          # skim: only if asked; scan/comment never
RATINGS = ("forgot", "hard", "medium", "easy")


def _load(path, table):
    if not path.is_file():
        return []
    with open(path, "rb") as fh:
        return tomllib.load(fh).get(table, [])


def _save(path, table, rows):
    atomic_write(path, tomli_w.dumps({table: rows}))


def list_path(lib):
    return lib.root / "reading" / "list.toml"


def recall_path(lib):
    return lib.root / "reading" / "recall.toml"


def load_list(lib):
    return _load(list_path(lib), "paper")


def load_cards(lib):
    return _load(recall_path(lib), "card")


def add_to_list(lib, key, depth, note="", today=None):
    if depth not in DEPTHS:
        raise ValueError(f"depth must be one of {', '.join(DEPTHS)}")
    key = lib.get(key).key
    rows = load_list(lib)
    for r in rows:
        if r["key"] == key:
            r["depth"] = depth
            break
    else:
        rows.append({"key": key, "depth": depth, "added": today or dt.date.today(),
                     "status": "to read", "note": note})
    _save(list_path(lib), "paper", rows)


def mark_done(lib, key, depth, today=None, prompt=""):
    """Record a finished paper; deep/read papers get a recall card."""
    today = today or dt.date.today()
    key = lib.get(key).key
    rows = load_list(lib)
    for r in rows:
        if r["key"] == key:
            r["status"] = "done"
            r["done"] = {"depth": depth, "date": today}
            break
    else:
        rows.append({"key": key, "depth": depth, "added": today, "status": "done",
                     "done": {"depth": depth, "date": today}})
    _save(list_path(lib), "paper", rows)
    if depth in RECALL_DEPTHS:
        cards = load_cards(lib)
        if not any(c["id"] == key for c in cards):
            cards.append({"id": key, "box": 1, "last_reviewed": today,
                          "next_due": next_working_day(today), "prompt": prompt})
            _save(recall_path(lib), "card", cards)


# ---- dates

def next_working_day(d: dt.date, n: int = 1) -> dt.date:
    while n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def off_weekend(d: dt.date) -> dt.date:
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def add_months(d: dt.date, m: int) -> dt.date:
    y, mo = divmod(d.month - 1 + m, 12)
    y, mo = d.year + y, mo + 1
    return dt.date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))


def interval(box: int, today: dt.date, medium: bool = False) -> dt.date:
    if box == 1:
        return next_working_day(today, 3 if medium else 1)
    if box == 2:
        return off_weekend(today + dt.timedelta(weeks=2 if medium else 1))
    if box == 3:
        return off_weekend(add_months(today, 2 if medium else 1))
    return off_weekend(add_months(today, 3))


def rate(lib, card_id: str, rating: str, today=None) -> dict:
    if rating not in RATINGS:
        raise ValueError(f"rating must be one of {', '.join(RATINGS)}")
    today = today or dt.date.today()
    cards = load_cards(lib)
    for c in cards:
        if c["id"] == card_id:
            box = int(c.get("box", 1))
            if rating == "easy":
                box = min(4, box + 1)
            elif rating == "forgot":
                box = 1
            c["box"] = box
            c["last_reviewed"] = today
            c["next_due"] = interval(box, today, medium=(rating == "medium"))
            _save(recall_path(lib), "card", cards)
            return c
    raise ValueError(f"no recall card {card_id!r}")


def due(lib, today=None) -> dict:
    today = today or dt.date.today()
    cards = [c for c in load_cards(lib) if c.get("next_due") and c["next_due"] <= today]
    reading = [r for r in load_list(lib) if r.get("status") == "reading"]
    return {"recall": cards, "reading": reading}
