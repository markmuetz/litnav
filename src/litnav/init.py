"""`litnav init <dir>`: create a new library."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import config
from .util import atomic_write

DIRS = ["papers", "notes", "concepts", "topics", "projects", "quizzes", "reading",
        "derived", "curation", "eval", "pdfs/_inbox", "pdfs/_variants", config.STATE_DIR]

GITIGNORE = """\
# litnav: PDFs and the index are not tracked
/pdfs/
/.litnav/
.DS_Store
"""

GITATTRIBUTES = """\
* text=auto eol=lf
*.pdf binary
"""

README = """\
# My litnav library

Managed with [litnav](https://github.com/markmuetz/litnav).

**Keep this repository private.** It holds the text extracted from your
papers (`papers/*/text.txt`). For papers you hold under a publisher or
university licence, publishing that text is redistribution. To publish this
repo, set `track_text = false` in `library.toml` first.

- `papers/<key>/`: one folder per paper (`ref.bib`, `meta.toml`, `text.txt`,
  `summary.json`)
- the PDFs, `<key>.pdf`: in `pdf_dir` (`library.toml`; default `pdfs/`, not tracked).
  New downloads go in its `_inbox/`.
- `notes/`, `concepts/`, `topics/`: your own writing. Cite papers as `[@key]`.
- `reading/`: reading list and recall state
"""

EVAL_QUESTIONS = """\
# Questions for `litnav eval`, which measures how well search finds the right
# papers. Write 20-50 real questions you would ask your library, each with the
# papers a good answer must find. Uncomment and copy the example.
#
# [[question]]
# q = "what sets the angle of a squall line to the low-level shear?"
# keys = ["smith2021cold"]           # papers a good answer must find
# passages = ["cold pool speed"]     # optional: words an expected passage contains
# kind = "paraphrase"                # exact-name, paraphrase or concept
# written_from = "memory"            # memory, notes or summary
"""

SYNCED = ("dropbox", "onedrive", "icloud", "mobile documents", "google drive", "box sync")


def in_synced_folder(p: Path) -> bool:
    s = str(p).lower()
    return any(x in s for x in SYNCED)


def init(target: Path, *, git: bool = True, licensed_to_models: bool = True, log=print) -> Path:
    root = Path(target).expanduser().resolve()
    if (root / config.CONFIG_NAME).exists():
        raise FileExistsError(f"{root} is already a litnav library")
    if root.exists() and any(p for p in root.iterdir() if p.name not in (".git", ".DS_Store")):
        raise FileExistsError(f"{root} exists and is not empty")
    for d in DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)
    cfg = config.LIBRARY_TOML
    if not licensed_to_models:
        cfg = cfg.replace("licensed_to_models = true\n", "licensed_to_models = false\n", 1)
    if in_synced_folder(root):
        # SQLite in a synced folder gets corrupted by the sync client.
        cache = Path.home() / ".cache" / "litnav" / root.name
        cfg = cfg.replace('mailto = ""\n',
                          f'mailto = ""\n# The library is in a synced folder; the index lives outside it.\n'
                          f'index_dir = "{cache.as_posix()}"\n', 1)
        log(f"note: library is in a synced folder; index will live in {cache}")
    atomic_write(root / config.CONFIG_NAME, cfg)
    atomic_write(root / ".gitignore", GITIGNORE)
    atomic_write(root / ".gitattributes", GITATTRIBUTES)
    atomic_write(root / "README.md", README)
    atomic_write(root / "eval" / "questions.toml", EVAL_QUESTIONS)
    for d in ("notes", "concepts", "topics", "projects", "quizzes", "reading", "derived",
              "curation", "eval", "papers"):
        keep = root / d / ".gitkeep"
        if not any((root / d).iterdir()):
            keep.touch()
    if git:
        _git_init(root, log)
    log(f"created library at {root}")
    return root


def _git_init(root: Path, log) -> None:
    g = shutil.which("git")
    if not g:
        log("warning: git not found; the library works without it, but has no history")
        return
    if not (root / ".git").exists():
        subprocess.run([g, "init", "-q"], cwd=root, check=True)
    subprocess.run([g, "add", "-A"], cwd=root, check=True)
    r = subprocess.run([g, "commit", "-q", "-m", "Create litnav library."], cwd=root,
                       capture_output=True, text=True)
    if r.returncode != 0:
        log("warning: could not make the first commit (is git user.name/user.email set?):\n"
            + r.stderr.strip())


QUESTION = """\
Papers you hold under a publisher or university licence are `licensed` (the
default sharing level). Ask, summaries and agents work by sending passages of
a paper's text to a model (Anthropic's, through the API or Claude Code). Some
publishers' text-mining terms restrict this; check your institution's
guidance if unsure. `open` papers are unaffected, and `confidential` ones
never leave this machine.
"""


def ask_licensed_to_models(input_=input, log=print) -> bool:
    """The one question `init` asks. The answer is stored in library.toml
    (licensed_to_models) and can be changed there later."""
    log(QUESTION)
    while True:
        a = input_("May the text of licensed papers be sent to a model? [Y/n] ").strip().lower()
        if a in ("", "y", "yes"):
            return True
        if a in ("n", "no"):
            return False
