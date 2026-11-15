"""BibTeX: parse one entry, normalise it to house style, export.

Normalisation is idempotent and has five passes:

1. delimiters  "X" -> {X};  "{X}" -> {{X}};  bare values stay bare, except a
               bare month name (June, Sept) -> its macro (jun, sep)
2. field order title, author, ... , url, doi last
3. layout      lowercase field names, 4-space indent, ` = `, no trailing comma
4. authors     tidied (no periods after initials, TN -> T N when safe), then
               canonicalised against the library's author authority, if any
5. journals    canonicalised against the library's journal authority, if any

Why "{X}" -> {{X}} and not {X}: BibTeX strips the *delimiter* braces before
change.case$ runs, so `title = {X}` is unprotected and a plain bibliography
style lowercases MJO, ENSO, WSR-88D. Only *content* braces protect.

Why bare values stay bare: `month = jan` is a macro; bracing it makes the
literal string "jan".

Every value must survive normalisation apart from its delimiters and the
known substitutions; if one would not, normalise() returns None and the
file is left alone.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

ORDER = ["title", "author", "editor", "journal", "booktitle", "series", "volume",
         "number", "issue", "pages", "year", "month", "publisher", "institution",
         "organization", "school", "address", "edition", "chapter", "howpublished",
         "note", "abstract", "keywords", "issn", "isbn", "eprint", "archiveprefix"]
TAIL = ["url", "doi"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

_FIELD = re.compile(r"\s*([A-Za-z][A-Za-z0-9_:-]*)\s*=\s*")
_HEAD = re.compile(r"\s*@(\w+)\s*\{\s*([^,\s]+)\s*,")


@dataclass
class Entry:
    type: str
    key: str
    fields: list[tuple[str, str]]        # (lowercased name, raw value with delimiters)
    preamble: str = ""

    def raw(self, name: str) -> str | None:
        for n, v in self.fields:
            if n == name:
                return v
        return None

    def get(self, name: str, default: str = "") -> str:
        """Value without its outer delimiter (content braces kept)."""
        v = self.raw(name)
        if v is None:
            return default
        return unwrap(v)

    def set(self, name: str, value: str) -> None:
        """Set a field from plain content; it is braced on output."""
        value = "{" + value + "}"
        for i, (n, _) in enumerate(self.fields):
            if n == name:
                self.fields[i] = (n, value)
                return
        self.fields.append((name, value))

    def authors(self) -> list[str]:
        a = self.get("author")
        return [x.strip() for x in re.split(r"\s+and\s+", a) if x.strip()] if a else []


def unwrap(v: str) -> str:
    v = v.strip()
    if (v.startswith("{") and v.endswith("}")) or (v.startswith('"') and v.endswith('"')):
        return v[1:-1]
    return v


def parse(text: str) -> Entry | None:
    # anything before the first @entry is a comment BibTeX ignores; it is
    # sometimes a human note recording where the citation came from -- keep it
    at = text.find("@")
    if at < 0:
        return None
    preamble, text = text[:at], text[at:]
    m = _HEAD.match(text)
    if not m:
        return None
    i, fields = m.end(), []
    while i < len(text):
        fm = _FIELD.match(text, i)
        if not fm:
            break
        name, j = fm.group(1), fm.end()
        if j >= len(text):
            break
        if text[j] == "{":
            d, k = 0, j
            for k in range(j, len(text)):
                if text[k] == "{":
                    d += 1
                elif text[k] == "}":
                    d -= 1
                    if d == 0:
                        break
            if d != 0:
                return None
            val, i = text[j:k + 1], k + 1
        elif text[j] == '"':
            k = text.find('"', j + 1)
            if k < 0:
                return None
            val, i = text[j:k + 1], k + 1
        else:
            k = j
            while k < len(text) and text[k] not in ",}\n":
                k += 1
            val, i = text[j:k].strip(), k
        fields.append((name.lower(), val))
        while i < len(text) and text[i] in ", \t\r\n":
            i += 1
        if i < len(text) and text[i] == "}":
            break
    return Entry(m.group(1).lower(), m.group(2), fields, preamble)


def parse_many(text: str) -> list[Entry]:
    """Split a multi-entry .bib on top-level @ and parse each."""
    out, depth, start = [], 0, None
    for i, c in enumerate(text):
        if c == "@" and depth == 0:
            if start is not None:
                e = parse(text[start:i])
                if e:
                    out.append(e)
            start = i
        elif c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
    if start is not None:
        e = parse(text[start:])
        if e:
            out.append(e)
    return [e for e in out if e.type not in ("comment", "preamble", "string")]


# ---------------------------------------------------------------- authority

def _norm_journal(x: str) -> str:
    """Dots stripped, whitespace collapsed, uppercased: `J. Atmos. Sci.`,
    `J Atmos Sci` and `J ATMOS SCI` all hit the same entry."""
    x = re.sub(r"[{}\\]", "", x).replace("&amp;", "&")
    return re.sub(r"\s+", " ", re.sub(r"[.]", " ", x)).strip().upper()


@dataclass
class Authority:
    """Optional per-library rules. Authors are keyed on (surname, first
    initial), so a name never seen before still resolves; groups flagged
    `split` (several people share the key) are tidied but never merged."""
    authors: dict = field(default_factory=dict)
    author_split: set = field(default_factory=set)
    journals: dict = field(default_factory=dict)

    @classmethod
    def load(cls, curation_dir: Path | None) -> "Authority":
        a = cls()
        if not curation_dir:
            return a
        jp = curation_dir / "journal-canonical.tsv"
        if jp.is_file():
            for r in csv.DictReader(open(jp, encoding="utf-8"), delimiter="\t"):
                canon = (r.get("canonical") or "").strip()
                if not canon:
                    continue
                a.journals[_norm_journal(canon)] = canon
                for v in (r.get("variants") or "").split("|"):
                    if v.strip():
                        a.journals.setdefault(_norm_journal(v), canon)
        ap = curation_dir / "author-canonical.tsv"
        if ap.is_file():
            for r in csv.DictReader(open(ap, encoding="utf-8"), delimiter="\t"):
                r = {k: (v or "") for k, v in r.items()}
                k = (r.get("surname", "").lower(), r.get("initial", ""))
                if "split" in r.get("flag", "").lower():
                    a.author_split.add(k)
                elif r.get("canonical"):
                    a.authors[k] = r["canonical"]
        return a


_EMPTY = Authority()


# ---------------------------------------------------------------- passes

def redelimit(v: str) -> str:
    if v.startswith('"') and v.endswith('"'):
        inner = v[1:-1]
        if inner.startswith("{") and inner.endswith("}"):
            return "{{" + inner[1:-1] + "}}"
        return "{" + inner + "}"
    return v


def tidy_name(sur: str, giv: str) -> str:
    """No periods after initials, jammed initials split (TN -> T N),
    hyphenated initials kept (J-F)."""
    out = []
    for tok in re.split(r"\s+", giv.strip()):
        t = tok.rstrip(".")
        if not t:
            continue
        if re.fullmatch(r"[A-Za-z]\.?-[A-Za-z]\.?", tok):
            out.append(f"{tok[0].upper()}-{tok.rstrip('.')[-1].upper()}")
        elif (len(t) > 1 and t.isupper() and "-" not in t
              and not sur.isupper() and len(t) <= 3):
            # TN -> T N, but ONLY when the surname is normally cased: some
            # publishers render whole names in caps (BASTIN, MARK E.), and
            # splitting there turns MARK into M A R K.
            out.extend(list(t))
        else:
            out.append(t)
    return f"{sur}, {' '.join(out)}".rstrip(", ")


def fix_authors(v: str, auth: Authority) -> str:
    if not (v.startswith("{") and v.endswith("}")):
        return v
    out = []
    for part in re.split(r"(\s+and\s+)", v[1:-1]):
        if re.fullmatch(r"\s+and\s+", part):
            out.append(part)
            continue
        raw = re.sub(r"\s+", " ", part).strip().rstrip(",")
        if not raw or raw == "others" or raw.startswith("{"):
            out.append(part)        # `others`, or a braced corporate name
            continue
        if "," in raw:
            sur, giv = (raw.split(",", 1) + [""])[:2]
        else:
            sur, giv = raw.split()[-1], " ".join(raw.split()[:-1])
        sur, giv = sur.strip(), giv.strip()
        ini = next((c for c in giv if c.isalpha()), "").lower()
        k = (sur.lower(), ini)
        lead = part[:len(part) - len(part.lstrip())]
        trail = part[len(part.rstrip()):]
        if k in auth.author_split:
            out.append(lead + tidy_name(sur, giv) + trail)
        else:
            out.append(lead + (auth.authors.get(k) or tidy_name(sur, giv)) + trail)
    return "{" + "".join(out) + "}"


def fix_journal(v: str, auth: Authority) -> str:
    if not (v.startswith("{") and v.endswith("}")):
        return v
    cur = re.sub(r"\s+", " ", v[1:-1]).strip()
    hit = auth.journals.get(_norm_journal(cur))
    return "{" + hit + "}" if hit and hit != cur else v


def fix_month(v: str) -> str:
    """A bare month must be one of the twelve macros: `June` is an undefined
    macro, which BibTeX parsers raise on."""
    w = v.strip().lower()
    return w[:3] if re.fullmatch(r"[a-z]+", w) and w[:3] in MONTHS else v


def format_entry(e: Entry) -> str:
    body = ",\n".join(f"    {n} = {v}" for n, v in e.fields)
    return e.preamble + f"@{e.type}{{{e.key},\n" + body + "\n}\n"


def normalise_entry(e: Entry, auth: Authority = _EMPTY) -> Entry | None:
    seen = {n for n, _ in e.fields}
    order = ([f for f in ORDER if f in seen]
             + [n for n, _ in e.fields if n not in ORDER and n not in TAIL]
             + [f for f in TAIL if f in seen])
    byname = dict(e.fields)          # a duplicated field: the last one wins, as in BibTeX
    out = []
    for n in dict.fromkeys(order):
        v = redelimit(byname[n])
        if n == "author":
            v = fix_authors(v, auth)
        elif n == "journal":
            v = fix_journal(v, auth)
        elif n == "month":
            v = fix_month(v)
        elif n == "doi":
            v = _fix_doi(v)
        out.append((n, v))
    new = Entry(e.type, e.key, out, e.preamble)
    text = re.sub(r"\s+", " ", format_entry(new))
    for n, v in e.fields:
        core = re.sub(r"\s+", " ", v.strip('{}" ')).strip()
        if (len(core) > 3 and n not in ("author", "journal", "month", "doi")
                and core not in text):
            return None
    return new


def _fix_doi(v: str) -> str:
    """Strip resolver prefixes; DOIs are case-insensitive, stored lowercase."""
    inner = unwrap(v).strip()
    inner = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", inner, flags=re.I)
    return "{" + inner.lower() + "}"


def normalise(text: str, auth: Authority = _EMPTY) -> str | None:
    e = parse(text)
    if e is None:
        return None
    n = normalise_entry(e, auth)
    return format_entry(n) if n else None


def doi_of(e: Entry) -> str:
    return _fix_doi(e.raw("doi"))[1:-1] if e.raw("doi") else ""
