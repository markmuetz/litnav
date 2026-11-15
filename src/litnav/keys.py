"""Citation keys: <author><year><word>[<part>][_<marker>] (design § 4).

- author: first author's surname, accents folded to ASCII, lowercased, all
  non-letters dropped (van den Heever -> vandenheever, Volonté -> volonte).
- year: the year of the *issue*, which callers must supply (not online-first).
  It must be plausible (1800 .. next year); without that check, journal
  article numbers parse as keys (grl28889 = grl + 2888 + 9).
- word: the title's first *content* word (leading articles, prepositions,
  conjunctions, interrogatives, auxiliaries and pronouns skipped). `toward`
  is deliberately not a stop word.
- part: a digit for Part I/II/... papers. A numeric suffix never means
  "duplicate copy": that namespace belongs to parts.
- marker: _preprint, _supplement, _corrigendum, _draft, ... for a deliberate
  pair with another item.

Collisions with a *different* paper append the title's next content word.
Uniqueness is case-insensitive, because macOS and Windows filesystems are.
"""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata

STOP = frozenset({
    # articles
    "a", "an", "the",
    # leading prepositions
    "of", "in", "on", "at", "to", "by", "for", "from", "with", "into",
    "about", "over", "under", "via", "using",
    # conjunctions
    "and", "or", "but", "nor",
    # interrogatives
    "what", "why", "how", "which", "when", "where", "who", "whose",
    # auxiliaries and copulas
    "is", "are", "was", "were", "be", "do", "does", "did",
    "can", "could", "should", "would", "will", "may", "might", "must",
    # demonstratives / quantifiers that lead titles
    "this", "that", "these", "those", "some", "no", "not",
    # pronouns ("Can We Use 1D Models..." -> use)
    "we", "i", "you", "it", "they", "our", "my",
})

MARKERS = ("preprint", "supporting", "supplement", "poster", "slides", "draft",
           "manuscript", "corrigendum")

KEY_RE = re.compile(r"^(?P<author>[a-z]+)(?P<year>\d{4})(?P<rest>[A-Za-z][A-Za-z0-9]*)"
                    r"(?:_(?P<marker>[a-z]+))?$")

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}
_PART_RE = re.compile(r"\bpart\s+([ivx]+|\d)\b", re.I)
_LATEX_CMD = re.compile(r"\\[a-zA-Z]+\s*")


def fold(name: str) -> str:
    """Fold accents to ASCII rather than deleting them (Wåhlström ->
    wahlstrom, not whlstrm), then keep letters only."""
    name = _latex_accents(name)
    d = unicodedata.normalize("NFD", name)
    d = "".join(c for c in d if not unicodedata.combining(c))
    d = (d.replace("\u00df", "ss").replace("\u00f8", "o").replace("\u00d8", "o")
          .replace("\u0142", "l").replace("\u0141", "l").replace("\u00e6", "ae")
          .replace("\u0153", "oe").replace("\u0111", "d"))
    return re.sub(r"[^a-z]", "", d.lower())


_ACCENT_MAP = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302",
               "~": "\u0303", "c": "\u0327", "v": "\u030c", "=": "\u0304",
               "u": "\u0306", "H": "\u030b", ".": "\u0307", "r": "\u030a"}


def _latex_accents(s: str) -> str:
    r"""{\"u} -> ü, \'e -> é, {\o} -> ø, \ss -> ß; good enough for keys and display."""
    s = re.sub(r"\\(o|O)\b", lambda m: "\u00f8" if m.group(1) == "o" else "\u00d8", s)
    s = re.sub(r"\\ss\b", "\u00df", s)
    s = re.sub(r"\\aa\b", "\u00e5", s)
    s = re.sub(r"\\l\b", "\u0142", s)

    def acc(m):
        mark, ch = m.group(1), m.group(2)
        return unicodedata.normalize("NFC", ch + _ACCENT_MAP[mark])
    s = re.sub(r"\\([\"'`^~=.])\s*\{?([A-Za-z])\}?", acc, s)
    s = re.sub(r"\\([cvuHr])\s*\{([A-Za-z])\}", acc, s)
    s = re.sub(r"\\([cvuHr])\s+([A-Za-z])", acc, s)
    return s


def plain(s: str) -> str:
    """LaTeX-ish bib value -> plain text for matching and display."""
    s = _latex_accents(s)
    s = _LATEX_CMD.sub("", s)
    return re.sub(r"\s+", " ", s.replace("{", "").replace("}", "")).strip()


def words(title: str) -> list[str]:
    t = fold_text(plain(title))
    return re.sub(r"[^a-z0-9]+", " ", t).split()


def fold_text(s: str) -> str:
    d = unicodedata.normalize("NFD", s)
    return "".join(c for c in d if not unicodedata.combining(c)).lower()


def content_words(title: str, extra_stop=()) -> list[str]:
    """Title words after the leading function words, in order. Only *leading*
    stop words are skipped for the key word itself; later words are taken as
    they come (minus stop words) for collision suffixes."""
    stop = STOP | set(extra_stop)
    return [w for w in words(title) if w not in stop and not w.isdigit()]


def plausible_year(y) -> bool:
    try:
        y = int(y)
    except (TypeError, ValueError):
        return False
    return 1800 <= y <= _dt.date.today().year + 1


def part_number(title: str) -> int | None:
    m = _PART_RE.search(plain(title))
    if not m:
        return None
    v = m.group(1).lower()
    return int(v) if v.isdigit() else _ROMAN.get(v)


def surname(author_field: str) -> str:
    """First author's surname from a BibTeX author field."""
    first = re.split(r"\s+and\s+", author_field.strip(), maxsplit=1)[0].strip()
    first = first.strip("{}")
    if "," in first:
        return first.split(",", 1)[0].strip()
    parts = first.split()
    return parts[-1] if parts else ""


def derive(surname_: str, year, title: str, *, marker: str | None = None,
           extra_stop=(), part: bool = True) -> str | None:
    """The base key, or None if any component is missing or implausible."""
    a = fold(surname_)
    cw = content_words(title, extra_stop)
    if not a or not cw or not plausible_year(year):
        return None
    key = f"{a}{int(year)}{cw[0]}"
    if part:
        n = part_number(title)
        if n and not key[-1].isdigit():
            key += str(n)
    if marker:
        key += f"_{marker}"
    return key


def derive_unique(surname_: str, year, title: str, exists, *, marker=None,
                  extra_stop=()) -> str | None:
    """derive(), then on collision append the next content word(s). `exists`
    is called with a candidate key and must compare case-insensitively.
    As a last resort (title exhausted) a letter is appended."""
    base = derive(surname_, year, title, extra_stop=extra_stop)
    if base is None:
        return None
    suffix = f"_{marker}" if marker else ""
    if not exists(base + suffix):
        return base + suffix
    cw = content_words(title, extra_stop)
    a, y = fold(surname_), int(year)
    n = part_number(title)
    for i in range(2, len(cw) + 1):
        cand = f"{a}{y}{''.join(cw[:i])}" + (str(n) if n else "") + suffix
        if not exists(cand):
            return cand
    for letter in "bcdefghijklmnopqrstuvwxyz":
        cand = base + letter + suffix
        if not exists(cand):
            return cand
    return None


def validate(key: str) -> list[str]:
    """Problems with a key against the convention; [] if it conforms."""
    m = KEY_RE.match(key)
    if not m:
        return ["does not match <author><year><word>[_<marker>]"]
    probs = []
    if not plausible_year(m.group("year")):
        probs.append(f"implausible year {m.group('year')}")
    if m.group("marker") and m.group("marker") not in MARKERS:
        probs.append(f"unknown marker _{m.group('marker')}")
    return probs


def casefold_key(key: str) -> str:
    return key.casefold()
