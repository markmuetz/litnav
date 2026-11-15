"""Library location and configuration.

A library is a directory containing `library.toml`. Configuration is that
file, overlaid by `.litnav/local.toml` (machine-specific, gitignored), so a
second machine can clone the library and set only its own `pdf_dir`.
"""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

CONFIG_NAME = "library.toml"
STATE_DIR = ".litnav"

DEFAULTS: dict = {
    "library": {
        "pdf_dir": "pdfs",             # relative to the library root, or absolute
        "default_sharing": "licensed",
        "track_text": True,
        "licensed_to_models": True,
        "mailto": "",                  # for the Crossref/OpenAlex polite pools
    },
    "keys": {
        "style": "derive",             # "derive" or "keep" (take keys from imported .bib)
        "extra_stop_words": [],
    },
    "import": {
        "mode": "copy",                # "copy" or "move" files into pdf_dir
        "verify_identifier": 0.6,
        "verify_title_query": 0.75,
    },
    "extract": {
        "ocr": "auto",                 # "auto", "off", "tesseract", "vision"
    },
    "search": {
        "embedding_model": "BAAI/bge-small-en-v1.5",
    },
    "ask": {
        "backend": "claude-code",      # "claude-code" or "api"
        "model": "claude-opus-5-5",
    },
}

LIBRARY_TOML = '''\
# litnav library configuration. Machine-specific overrides go in
# .litnav/local.toml (not tracked), e.g. a different pdf_dir on another computer.

[library]
# Where PDFs live: relative to this directory, or an absolute path (e.g. a
# synced folder). Every PDF is <pdf_dir>/<key>.pdf.
pdf_dir = "pdfs"
# Sharing level for new items: open, licensed, private or confidential.
default_sharing = "licensed"
# Track extracted text (papers/<key>/text.txt) in git. Keep this library repo
# private if true: the text of licensed papers must not be published.
track_text = true
# May the text of `licensed` papers be sent to a model (Ask, summaries, agents)?
# Asked once by `litnav init`. `open` and `private` papers are unaffected;
# `confidential` papers never leave the machine.
licensed_to_models = true
# Email for the Crossref/OpenAlex polite pools (optional; sent with API requests).
mailto = ""

[keys]
# "derive": <author><year><word> from verified metadata.
# "keep": use the key from an imported .bib, validated only.
style = "derive"
extra_stop_words = []

[import]
mode = "copy"   # or "move"

[extract]
ocr = "auto"    # auto, off, tesseract, vision

[search]
embedding_model = "BAAI/bge-small-en-v1.5"

[ask]
backend = "claude-code"   # or "api" (needs ANTHROPIC_API_KEY)
model = "claude-opus-5-5"
'''


class LibraryNotFound(Exception):
    pass


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def find_root(explicit: str | os.PathLike | None = None, start: Path | None = None) -> Path:
    """--library, then $LITNAV_LIBRARY, then walk up from the cwd."""
    for cand in (explicit, os.environ.get("LITNAV_LIBRARY")):
        if cand:
            p = Path(cand).expanduser().resolve()
            if (p / CONFIG_NAME).is_file():
                return p
            raise LibraryNotFound(f"{p} is not a litnav library (no {CONFIG_NAME})")
    p = (start or Path.cwd()).resolve()
    for d in (p, *p.parents):
        if (d / CONFIG_NAME).is_file():
            return d
    raise LibraryNotFound(
        "no litnav library here: run `litnav init <dir>`, pass --library, "
        "or set LITNAV_LIBRARY")


def load(root: Path) -> dict:
    cfg = DEFAULTS
    for f in (root / CONFIG_NAME, root / STATE_DIR / "local.toml"):
        if f.is_file():
            with open(f, "rb") as fh:
                cfg = _merge(cfg, tomllib.load(fh))
    return cfg


def pdf_dir(root: Path, cfg: dict) -> Path:
    p = Path(cfg["library"]["pdf_dir"]).expanduser()
    return p if p.is_absolute() else root / p
