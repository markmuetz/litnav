"""Import PDFs: plan (dry run) -> review -> apply.

    litnav import                 plan for everything in <pdf_dir>/_inbox/
    litnav import --apply         ... and do it
    litnav import --from-plan     apply exactly the last plan, no re-querying
    litnav import FILE... [--doi D] [--key K] [--tag T] [--apply]

Subdirectories of _inbox/ are tags. `_inbox/_confidential/` is special: those
files are never read or looked up (no text extraction, no Crossref); they need
a key, from the filename or --key, and are imported as confidential.

Before anything is created, duplicates are found by PDF hash, DOI and
normalised title -- not just by key -- so the same paper under another
filename is caught. Outcomes:
  ok              imported as a new item
  held            the library already has this paper: the file goes to
                  _variants/<key>.<date>.pdf (never deleted)
  low-confidence  metadata found but not verified in the text: left in place
  no-match        nothing found: left in place

A paper with no DOI or arXiv id (an accepted manuscript, a report) is
imported with its metadata given by hand (`--title --author --year
[--journal]`, single file). Nothing is looked up; the title is still checked
against the PDF's opening text, so the wrong file is caught. `litnav check`
lists such articles ("article without a DOI") until a DOI is added.
"""
from __future__ import annotations

import csv
import datetime as dt
import re
import shutil
from dataclasses import asdict, fields
from pathlib import Path

from . import bib, keys
from .identify import Proposal, identify, verify
from .net import Client
from .util import sha256_file

CONFIDENTIAL_DIR = "_confidential"
PLAN_FIELDS = [f.name for f in fields(Proposal) if f.name != "extra"] + ["tag", "path", "dup_of", "sha256"]


def norm_title(t: str) -> str:
    return " ".join(keys.words(t))


class Catalogue:
    """What the library already holds, for duplicate detection."""

    def __init__(self, lib):
        self.lib = lib
        self.by_doi, self.by_title, self.by_hash, self.by_tya = {}, {}, {}, {}
        for it in lib.items():
            if it.title:
                self.by_tya.setdefault(self._tya(it.title, it.year, it.authors[0] if it.authors else ""), it.key)
            if it.doi:
                self.by_doi[it.doi.lower()] = it.key
            if it.title:
                self.by_title.setdefault(norm_title(it.title), it.key)
            h = it.meta.get("files", {}).get("pdf_sha256")
            if h:
                self.by_hash[h] = it.key
        self.batch_keys: set[str] = set()

    def exists(self, key: str) -> bool:
        return self.lib.exists(key) or key.casefold() in self.batch_keys

    def duplicate(self, p: Proposal, sha: str) -> str | None:
        if sha in self.by_hash:
            return self.by_hash[sha]
        if p.doi and p.doi.lower() in self.by_doi:
            # same DOI but one is a preprint (same-DOI kinds, e.g. AMS EOR) is
            # a deliberate pair, not a duplicate
            held = self.by_doi[p.doi.lower()]
            if not (held.endswith("_preprint") ^ p.key.endswith("_preprint")):
                return held
        if p.title:
            held = self.by_title.get(norm_title(p.title)) if len(p.title) > 20 else None
            # short titles ("Clouds") only count with the same year and first author
            held = held or self.by_tya.get(self._tya(p.title, p.year, p.extra.get("author", "")))
            if held and not (held.endswith("_preprint") ^ p.key.endswith("_preprint")):
                return held
        return None

    @staticmethod
    def _tya(title, year, author):
        return (norm_title(title), str(year or ""), keys.fold(keys.surname(author or "")))

    def add(self, p: Proposal, sha: str):
        self.batch_keys.add(p.key.casefold())
        self.by_hash[sha] = p.key
        if p.doi:
            self.by_doi.setdefault(p.doi.lower(), p.key)
        if p.title:
            self.by_title.setdefault(norm_title(p.title), p.key)
            self.by_tya.setdefault(self._tya(p.title, p.year, p.extra.get("author", "")), p.key)


def manual_bibtex(key: str, manual: dict) -> str:
    """A ref.bib entry from metadata given by hand: title, author (BibTeX
    form, `Family, Given and ...`), year, and optionally journal."""
    from .sources.crossref import tex
    f = [("title", tex(manual["title"])), ("author", tex(manual["author"]))]
    if manual.get("journal"):
        f.append(("journal", tex(manual["journal"])))
    f.append(("year", str(manual["year"])))
    body = ",\n".join(f"    {n} = {{{v}}}" for n, v in f)
    etype = "article" if manual.get("journal") else "misc"
    return f"@{etype}{{{key},\n{body}\n}}\n"


def check_manual(manual: dict) -> dict:
    missing = [k for k in ("title", "author", "year") if not manual.get(k)]
    if missing:
        raise ValueError("metadata by hand needs --title, --author and --year (missing: "
                         + ", ".join("--" + k for k in missing) + ")")
    if not keys.plausible_year(manual["year"]):
        raise ValueError(f"implausible year {manual['year']!r}")
    return manual


def _manual_proposal(lib, path: Path, manual: dict, key: str | None, cat, *, read: bool) -> Proposal:
    """No lookup. Unless `read` is False (confidential), the title must be
    found in the PDF's opening text, as for an identifier match."""
    from . import extract
    p = Proposal(file=path.name, via="manual", title=manual["title"], year=int(manual["year"]),
                 extra={"author": manual["author"]})
    p.key = key or keys.derive_unique(keys.surname(manual["author"]), manual["year"],
                                      manual["title"], cat.exists,
                                      extra_stop=lib.config["keys"].get("extra_stop_words", [])) or ""
    if not p.key or keys.validate(p.key):
        p.status = "no-key"
        return p
    p.bibtex = manual_bibtex(p.key, manual)
    if key and cat.exists(key):
        p.status = "collides"
        return p
    if read:
        text, _ = extract.head(path, pages=5)
        p.score = verify(p.title, text)
        bar = lib.config.get("import", {}).get("verify_identifier", 0.6)
        p.status = "ok" if p.score >= bar else "low-confidence"
    else:
        p.status = "ok"
    return p


def inbox_files(lib) -> list[tuple[Path, str]]:
    """(path, tag) for every PDF in the inbox; tag is the first subdirectory."""
    out = []
    if not lib.inbox.is_dir():
        return out
    for p in sorted(lib.inbox.rglob("*")):
        if p.is_file() and p.suffix.lower() == ".pdf":
            rel = p.relative_to(lib.inbox)
            tag = rel.parts[0] if len(rel.parts) > 1 else ""
            if tag.casefold().lstrip("_") == "confidential":
                tag = CONFIDENTIAL_DIR           # _confidential, confidential, _Confidential ...
            out.append((p, tag))
    return out


def plan(lib, files: list[tuple[Path, str]], *, doi=None, key=None, manual=None,
         client=None, log=print) -> list[dict]:
    if manual:
        check_manual(manual)
        if len(files) != 1 or doi:
            raise ValueError("metadata by hand is for a single file, without --doi")
    client = client if (client or manual) else Client.for_library(lib)
    cat = Catalogue(lib)
    rows = []
    for i, (path, tag) in enumerate(files, 1):
        sha = sha256_file(path)
        if tag == CONFIDENTIAL_DIR:
            if manual:          # never read: the title is not checked against the text
                p = _manual_proposal(lib, path, manual, key, cat, read=False)
                p.via = "confidential+manual"
            else:
                p = Proposal(file=path.name, via="confidential")
                k = key or path.stem
            if manual:
                pass
            elif keys.validate(k):
                p.status = "needs-key"
            elif cat.exists(k):
                p.status, p.key = "collides", k
            else:
                p.status, p.key = "ok", k
            tag = ""
            conf = True
        else:
            conf = False
            if manual:
                p = _manual_proposal(lib, path, manual, key, cat, read=True)
            elif key:
                p = identify(path, client, lambda k_: False, cfg=lib.config, doi=doi)
                if p.status in ("ok", "low-confidence") and p.bibtex:
                    e = bib.parse(p.bibtex)
                    e.key = key
                    p.bibtex = bib.format_entry(e)
                p.key = key
                if cat.exists(key):
                    p.status = "collides"
            else:
                p = identify(path, client, cat.exists, cfg=lib.config, doi=doi)
        dup = None if conf else cat.duplicate(p, sha)
        if dup and p.status in ("ok", "low-confidence", "collides"):
            p.status = "held"
        if p.status == "ok":
            cat.add(p, sha)
        row = {k: v for k, v in asdict(p).items() if k != "extra"}
        row.update(tag=tag, path=str(path), dup_of=dup or "", sha256=sha,
                   score=f"{p.score:.2f}", confidential="yes" if conf else "")
        rows.append(row)
        log(f"  [{i}/{len(files)}] {path.name[:44]:<46} {p.key or '-':<34} {p.status}"
            + (f" (= {dup})" if dup else ""))
    return rows


def plan_path(lib) -> Path:
    return lib.index_dir / "import-plan.tsv"


def write_plan(lib, rows):
    pp = plan_path(lib)
    pp.parent.mkdir(parents=True, exist_ok=True)
    cols = PLAN_FIELDS + ["confidential"]
    with open(pp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t", lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (str(v).replace("\n", "\\n") if v is not None else "")
                        for k, v in r.items()})


def read_plan(lib) -> list[dict]:
    with open(plan_path(lib), encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    for r in rows:
        r["bibtex"] = r.get("bibtex", "").replace("\\n", "\n")
    return rows


def apply(lib, rows, *, tags=(), mode: str | None = None, client=None, log=print) -> dict:
    """Import the planned rows. With a client, each new (non-confidential) paper
    also gets its public abstract looked up (abstracts.py)."""
    mode = mode or lib.config["import"].get("mode", "copy")
    counts = {"imported": 0, "held": 0, "left": 0}
    lib.pdf_dir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        src = Path(r["path"])
        if not src.is_file():
            continue
        inbox = _under(src, lib.inbox)
        mover = shutil.move if (mode == "move" or inbox) else shutil.copy2
        if r["status"] == "ok":
            conf = r.get("confidential") == "yes"
            t = [x for x in [r.get("tag")] + list(tags) if x]
            it = lib.create_item(r["key"], r.get("bibtex") or None, tags=t,
                                 sharing_level="confidential" if conf else None)
            if conf:
                it.add_tag("confidential")
            dest = it.pdf_path
            if dest.exists():
                raise FileExistsError(f"{dest} exists but {r['key']} was a new item")
            mover(str(src), str(dest))
            m = it.meta
            m.setdefault("files", {})["pdf_sha256"] = r["sha256"]
            if r.get("arxiv"):
                m.setdefault("ids", {})["arxiv"] = r["arxiv"]
            if r.get("via"):
                m.setdefault("files", {})["identified_via"] = r["via"]
            if r.get("licence") and not conf:
                from . import licences
                c = licences.code(r["licence"])
                m["licence"] = {"source": "crossref", "checked": dt.date.today().isoformat(),
                                "url": r["licence"], "code": c}
                if c in licences.open_codes(lib) and it.sharing == "licensed":
                    m["sharing"] = "open"
                    log(f"  {r['key']}: {c} licence, so sharing is open")
            it.save_meta()
            if not conf:
                from .extract import extract_item
                try:
                    extract_item(lib.get(r["key"]), log=log)
                except Exception as e:          # a broken PDF must not stop the import
                    log(f"  {r['key']}: no text ({e}); try `litnav extract {r['key']}`")
                _after_import(lib, r["key"], client, log)
            counts["imported"] += 1
            log(f"  imported {r['key']}")
        elif r["status"] == "held" and r.get("dup_of"):
            lib.variants.mkdir(parents=True, exist_ok=True)
            dest = _free(lib.variants / f"{r['dup_of']}.{dt.date.today():%Y-%m-%d}.pdf")
            mover(str(src), str(dest))
            counts["held"] += 1
            log(f"  held already as {r['dup_of']}: file kept as _variants/{dest.name}")
        else:
            counts["left"] += 1
    if counts["imported"]:
        from . import discovery
        for r in discovery.prune(lib):
            log(f"to-fetch: {r['doi']} is now in the library; row removed")
    return counts


def _after_import(lib, key, client, log):
    """A new paper's licence line in its own text (Crossref misses some), and
    its public abstract. Neither may stop the import."""
    from . import abstracts, licences
    it = lib.get(key)
    if it.sharing == "licensed" and it.has_text and "code" not in it.meta.get("licence", {}):
        got = licences.from_text(it.text())
        if got and got[1] == "article":
            it.meta["licence"] = {"source": "text", "checked": dt.date.today().isoformat(),
                                  "code": got[0]}
            if got[0] in licences.open_codes(lib):
                it.meta["sharing"] = "open"
                log(f"  {key}: {got[0]} licence line in its text, so sharing is open")
            it.save_meta()
    if client is None:
        return
    try:
        aid = it.meta.get("ids", {}).get("arxiv", "")
        if it.doi or aid:
            ab, src, url, tried = abstracts.lookup_one(client, it.doi, aid)
            abstracts.write(it, ab, src, url, tried)
            log(f"  {key}: abstract from {src}" if ab else f"  {key}: no public abstract found")
    except Exception as e:                       # a lookup must not stop the import
        log(f"  {key}: abstract lookup failed ({e}); try `litnav abstracts {key}`")


def _under(p: Path, d: Path) -> bool:
    try:
        p.resolve().relative_to(d.resolve())
        return True
    except ValueError:
        return False


def _free(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(2, 1000):
        q = p.with_name(f"{p.stem}-{i}{p.suffix}")
        if not q.exists():
            return q
    raise FileExistsError(p)


SENSITIVE = re.compile(r"draft|manuscript|review|submission|revis|confidential|thesis|dissertation",
                       re.I)


def downloads(lib, *, since_hours: float = 2.0, folder: Path | None = None,
              log=print) -> list[Path]:
    """Recent PDFs in the downloads folder, one per paper: a browser's
    re-download (`name-2.pdf`, `name (1).pdf`) that is byte-identical to
    another file is skipped. Files are compared, never read as documents."""
    import filecmp
    import time
    folder = folder or Path(lib.config["import"].get("downloads_dir") or "~/Downloads").expanduser()
    if not folder.is_dir():
        raise ValueError(f"no downloads folder {folder}")
    cutoff = time.time() - since_hours * 3600
    pdfs = sorted((p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() == ".pdf" and p.stat().st_mtime >= cutoff),
                  key=lambda p: (len(p.name), p.name))     # `x.pdf` before `x-2.pdf`
    keep: list[Path] = []
    for p in pdfs:
        if SENSITIVE.search(p.stem):
            log(f"  {p.name}: name suggests a draft, manuscript or review; skipped "
                "(import it by name if it is a published paper)")
            continue
        same = next((q for q in keep if q.stat().st_size == p.stat().st_size
                     and filecmp.cmp(q, p, shallow=False)), None)
        if same:
            log(f"  {p.name}: same file as {same.name}; skipped")
        else:
            keep.append(p)
    return sorted(keep, key=lambda p: p.stat().st_mtime)
