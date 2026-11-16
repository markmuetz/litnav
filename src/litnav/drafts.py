"""Co-authored drafts: filing a new version without reading it.

A draft is a confidential item (key `<lead-author><year><word>_draft`, tag
`coauthor_draft`). `<pdf_dir>/<key>.pdf` is always the version being read
now. When a new version arrives:

    <pdf_dir>/<key>.pdf  ->  <pdf_dir>/_variants/<key>.<received>[-<label>].pdf
    new file             ->  <pdf_dir>/<key>.pdf
    curation/drafts.tsv  +=  one row for the new version

Only files are moved. The PDF is never opened, its text never extracted, and
nothing is looked up; its SHA-256 is recorded in meta.toml as for any PDF.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import shutil
from dataclasses import dataclass
from pathlib import Path

from .util import atomic_write, sha256_file

COLUMNS = ["draft_key", "version", "received", "source", "link", "my_comments",
           "published_key", "note"]


def log_path(lib) -> Path:
    return lib.root / "curation" / "drafts.tsv"


def load(lib) -> list[dict]:
    p = log_path(lib)
    if not p.is_file():
        return []
    return list(csv.DictReader(io.StringIO(p.read_text("utf-8")), delimiter="\t"))


def is_draft(it) -> bool:
    return it.key.endswith("_draft") or "coauthor_draft" in it.tags


@dataclass
class Plan:
    key: str
    new: Path
    dest: Path
    old: Path | None
    old_to: Path | None
    row: dict

    def describe(self) -> list[str]:
        out = []
        if self.old:
            out.append(f"current version: {self.old.name} -> _variants/{self.old_to.name}")
        out.append(f"new version: {self.new} -> {self.dest}")
        out.append(f"drafts.tsv: + {self.row['draft_key']} version {self.row['version']} "
                   f"received {self.row['received']} ({self.row['source']})")
        return out


def _date(s, what):
    try:
        return dt.date.fromisoformat(s).isoformat()
    except ValueError:
        raise ValueError(f"{what} must be YYYY-MM-DD, not {s!r}")


def plan_version(lib, key, pdf, *, received=None, version=None, label=None, source="pdf",
                 link="", note="") -> Plan:
    it = lib.get(key)
    if not is_draft(it):
        raise ValueError(f"{it.key} is not a co-authored draft (no _draft suffix or "
                         "coauthor_draft tag)")
    new = Path(pdf).expanduser().resolve()
    if not new.is_file():
        raise ValueError(f"no file {new}")
    if new.suffix.lower() != ".pdf":
        raise ValueError(f"{new.name} is not a PDF")
    received = _date(received, "--received") if received else dt.date.today().isoformat()
    dest = it.pdf_path
    if new == dest.resolve():
        raise ValueError(f"{new} is already the current version")
    old = old_to = None
    if dest.is_file():
        old = dest
        prev = [r for r in load(lib) if r.get("draft_key") == it.key and r.get("received")]
        when = prev[-1]["received"] if prev else \
            dt.date.fromtimestamp(dest.stat().st_mtime).isoformat()
        name = f"{it.key}.{when}" + (f"-{label}" if label else "") + ".pdf"
        old_to = _free(lib.variants / name)
    row = {"draft_key": it.key, "version": version or received, "received": received,
           "source": source, "link": link, "my_comments": "", "published_key": "", "note": note}
    return Plan(it.key, new, dest, old, old_to, row)


def apply(lib, plan: Plan) -> None:
    if plan.old:
        plan.old_to.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(plan.old), str(plan.old_to))
    plan.dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(plan.new), str(plan.dest))
    it = lib.get(plan.key)
    it.meta.setdefault("files", {})["pdf_sha256"] = sha256_file(plan.dest)
    it.save_meta()
    rows = load(lib)
    cols = list(rows[0].keys()) if rows else COLUMNS
    cols += [c for c in COLUMNS if c not in cols]
    buf = io.StringIO()
    w = csv.DictWriter(buf, cols, delimiter="\t", lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows + [plan.row]:
        w.writerow({c: (r.get(c) or "") for c in cols})
    atomic_write(log_path(lib), buf.getvalue())


def _free(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(2, 1000):
        q = p.with_name(f"{p.stem}-{i}{p.suffix}")
        if not q.exists():
            return q
    raise FileExistsError(p)
