"""The library: a directory of items, one per citation key.

    <root>/papers/<key>/ref.bib      bibliographic record (house style)
    <root>/papers/<key>/meta.toml    tags, sharing, ids, relations, files
    <root>/papers/<key>/text.txt     extracted text (raw; sections found at index time)
    <root>/papers/<key>/summary.json generated summary
    <pdf_dir>/<key>.pdf              the PDF

All access that could send text out or serve it to someone else goes
through `Library.items(purpose=...)` / `Item.allowed(purpose)`.
"""
from __future__ import annotations

import json
import re
from functools import cached_property
from pathlib import Path

from . import bib, config, keys, meta
from . import sharing as S
from .util import atomic_write

_UNSAFE = re.compile(r'[\\/:*?"<>|\s]')


class KeyError_(Exception):
    pass


class Item:
    def __init__(self, lib: "Library", key: str):
        self.lib, self.key = lib, key
        self.dir = lib.papers_dir / key

    def __repr__(self):
        return f"Item({self.key!r})"

    # -- files
    @property
    def bib_path(self) -> Path: return self.dir / "ref.bib"
    @property
    def meta_path(self) -> Path: return self.dir / "meta.toml"
    @property
    def text_path(self) -> Path: return self.dir / "text.txt"
    @property
    def summary_path(self) -> Path: return self.dir / "summary.json"
    @property
    def pdf_path(self) -> Path:
        """<pdf_dir>/<key>.pdf, unless meta [files] pdf_path names another
        file inside pdf_dir (for collections kept in a subfolder)."""
        rel = self.meta.get("files", {}).get("pdf_path")
        return self.lib.pdf_dir / (rel or f"{self.key}.pdf")

    @property
    def has_pdf(self) -> bool: return self.pdf_path.is_file()
    @property
    def has_text(self) -> bool: return self.text_path.is_file()

    # -- content
    @cached_property
    def entry(self) -> bib.Entry | None:
        if not self.bib_path.is_file():
            return None
        return bib.parse(self.bib_path.read_text("utf-8", "replace"))

    @cached_property
    def meta(self) -> dict:
        return meta.read(self.meta_path)

    def reload(self):
        self.__dict__.pop("entry", None)
        self.__dict__.pop("meta", None)

    @property
    def sharing(self) -> str:
        return self.meta.get("sharing") or self.lib.config["library"]["default_sharing"]

    @property
    def tags(self) -> list[str]:
        return list(self.meta.get("tags", []))

    def allowed(self, purpose: str) -> bool:
        return S.allow(self.sharing, self.lib.purpose(purpose))

    @property
    def title(self) -> str:
        return keys.plain(self.entry.get("title")) if self.entry else ""

    @property
    def year(self) -> str:
        return self.entry.get("year") if self.entry else ""

    @property
    def authors(self) -> list[str]:
        return [keys.plain(a) for a in self.entry.authors()] if self.entry else []

    @property
    def doi(self) -> str:
        return bib.doi_of(self.entry) if self.entry else ""

    @property
    def journal(self) -> str:
        if not self.entry:
            return ""
        return keys.plain(self.entry.get("journal") or self.entry.get("booktitle"))

    def summary(self) -> dict | None:
        if not self.summary_path.is_file():
            return None
        try:
            return json.loads(self.summary_path.read_text("utf-8"))
        except json.JSONDecodeError:
            return None

    def text(self) -> str:
        return self.text_path.read_text("utf-8", "replace") if self.has_text else ""

    # -- writes
    def save_meta(self):
        meta.write(self.meta_path, self.meta)

    def write_bib(self, text: str):
        n = bib.normalise(text, self.lib.authority)
        atomic_write(self.bib_path, n if n is not None else text)
        self.__dict__.pop("entry", None)

    def add_tag(self, tag: str):
        tag = tag.strip()
        if not tag or "," in tag or re.search(r"\s", tag):
            raise ValueError(f"bad tag {tag!r}: no spaces or commas")
        t = self.meta.setdefault("tags", [])
        if tag not in t:
            t.append(tag)
            self.save_meta()

    def remove_tag(self, tag: str):
        t = self.meta.setdefault("tags", [])
        if tag in t:
            t.remove(tag)
            self.save_meta()

    def set_sharing(self, level: str):
        self.meta["sharing"] = S.check_level(level)
        self.save_meta()

    def as_dict(self, purpose: str = S.LOCAL) -> dict:
        d = {"key": self.key, "title": self.title, "authors": self.authors,
             "year": self.year, "journal": self.journal, "doi": self.doi,
             "tags": self.tags, "sharing": self.sharing, "has_pdf": self.has_pdf,
             "has_text": self.has_text,
             "relations": self.meta.get("relations", {})}
        s = self.summary()
        if s and self.allowed(purpose):
            d["summary"] = s.get("summary", "")
            d["topic"] = s.get("topic", "")
        return d


class Library:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.config = config.load(self.root)
        self.papers_dir = self.root / "papers"
        self.pdf_dir = config.pdf_dir(self.root, self.config)
        self.state_dir = self.root / config.STATE_DIR
        self._aliases: dict[str, str] | None = None
        self._fold: dict[str, str] | None = None
        self._seen: int | None = None      # papers/ mtime when the caches were built
        # The owner's answer at `init`: may licensed papers' text go to a model?
        self.licensed_to_models = bool(self.config["library"].get("licensed_to_models", True))

    def purpose(self, purpose: str) -> str:
        """Map a purpose through this library's policy (see sharing.effective)."""
        return S.effective(purpose, self.licensed_to_models)

    @classmethod
    def find(cls, explicit=None) -> "Library":
        return cls(config.find_root(explicit))

    @cached_property
    def authority(self) -> bib.Authority:
        return bib.Authority.load(self.root / "curation")

    @property
    def inbox(self) -> Path: return self.pdf_dir / "_inbox"
    @property
    def variants(self) -> Path: return self.pdf_dir / "_variants"

    @property
    def index_dir(self) -> Path:
        d = self.config["library"].get("index_dir")
        return Path(d).expanduser() if d else self.state_dir

    # -- enumeration
    def keys(self) -> list[str]:
        if not self.papers_dir.is_dir():
            return []
        return sorted(p.name for p in self.papers_dir.iterdir()
                      if p.is_dir() and not p.name.startswith("."))

    def items(self, purpose: str = S.LOCAL, tag: str | None = None):
        for k in self.keys():
            it = Item(self, k)
            if not it.allowed(purpose):
                continue
            if tag and tag not in it.tags:
                continue
            yield it

    def alias_map(self) -> dict[str, str]:
        if self._aliases is None:
            m = {}
            for k in self.keys():
                for a in meta.read(self.papers_dir / k / "meta.toml").get("aliases", []):
                    m[a.casefold()] = k
            self._aliases = m
        return self._aliases

    def _folded(self) -> dict[str, str]:
        if self._fold is None:
            self._fold = {k.casefold(): k for k in self.keys()}
        return self._fold

    def invalidate(self):
        self._aliases = self._fold = None

    def _fresh(self) -> None:
        """Drop the key and alias caches if papers/ changed (an import or rename
        by another process, e.g. the CLI while the web app or MCP server runs)."""
        try:
            m = self.papers_dir.stat().st_mtime_ns
        except FileNotFoundError:
            m = None
        if m != self._seen:
            self._aliases = self._fold = None
            self._seen = m

    def resolve(self, key: str) -> str | None:
        """Exact key, case-insensitive key, or alias."""
        self._fresh()
        cf = key.casefold()
        return self._folded().get(cf) or self.alias_map().get(cf)

    def get(self, key: str) -> Item:
        k = self.resolve(key)
        if k is None:
            raise KeyError_(f"no item {key!r}")
        return Item(self, k)

    def exists(self, key: str) -> bool:
        """Case-insensitive, including aliases: what a new key must avoid."""
        return self.resolve(key) is not None

    # -- creation
    def create_item(self, key: str, bib_text: str | None = None, *,
                    tags=(), sharing_level: str | None = None) -> Item:
        if not key or _UNSAFE.search(key):
            raise KeyError_(f"key {key!r} is not safe as a filename")
        if self.exists(key):
            raise KeyError_(f"key {key!r} already exists (keys are case-insensitive)")
        it = Item(self, key)
        it.dir.mkdir(parents=True)
        if bib_text:
            e = bib.parse(bib_text)
            if e is not None and e.key != key:
                e.key = key
                bib_text = bib.format_entry(e)
            it.write_bib(bib_text)
        m = meta.empty()
        m["added"] = meta.today()
        if sharing_level:
            m["sharing"] = S.check_level(sharing_level)
        m["tags"] = list(dict.fromkeys(tags))
        it.__dict__["meta"] = m
        it.save_meta()
        self.invalidate()
        return it
