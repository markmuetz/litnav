"""Paper text, figure crops and PDFs stay out of the library's git history."""
import shutil
import subprocess

import pytest

from litnav import cli, githygiene as G
from litnav.init import init
from litnav.library import Library

from conftest import BIB

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="needs git")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


@pytest.fixture
def glib(tmp_path, monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.org",
                 "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.org"}.items():
        monkeypatch.setenv(k, v)
    root = init(tmp_path / "lib", git=True, log=lambda *a: None)
    lib = Library(root)
    lic = lib.create_item("smith2021cold", BIB, sharing_level="licensed")
    lic.text_path.write_text("Licensed words.")
    op = lib.create_item("jones2019gravity", BIB.replace("smith2021cold", "jones2019gravity"),
                         sharing_level="open")
    op.text_path.write_text("Open words.")
    return lib


def test_new_library_ignores_text_and_has_the_hook(glib):
    assert not G.track_text(glib)
    assert "papers/*/text.txt" in (glib.root / ".gitignore").read_text()
    assert G.HOOK_MARK in G.hook_path(glib.root).read_text()
    git(glib.root, "add", "-A")
    staged = G.staged(glib.root)
    assert "papers/smith2021cold/ref.bib" in staged
    assert not [p for p in staged if p.endswith("text.txt")]


def test_hook_refuses_forced_text_figures_and_pdfs(glib):
    figs = glib.root / "notes" / "figs"
    figs.mkdir(parents=True)
    (figs / "smith2021cold_fig2.png").write_bytes(b"png")
    (figs / "jones2019gravity_fig1.png").write_bytes(b"png")
    (glib.root / "topics").mkdir(exist_ok=True)
    (glib.root / "topics" / "smith2021cold.pdf").write_bytes(b"%PDF")
    git(glib.root, "add", "-f", "papers/smith2021cold/text.txt", "notes/figs", "topics/smith2021cold.pdf")
    bad = G.problems(glib, G.staged(glib.root))
    assert any("smith2021cold/text.txt" in b for b in bad)
    assert any("smith2021cold_fig2" in b for b in bad)
    assert not any("jones2019gravity_fig1" in b for b in bad)          # open paper: fine
    assert any("PDF of smith2021cold" in b for b in bad)
    r = git(glib.root, "commit", "-m", "x")
    assert r.returncode != 0 and "git-check" in r.stderr
    git(glib.root, "restore", "--staged", "papers/smith2021cold/text.txt", "notes/figs/smith2021cold_fig2.png",
        "topics/smith2021cold.pdf")
    assert git(glib.root, "commit", "-q", "-m", "ok").returncode == 0


def test_track_text_true_allows_text_but_not_confidential(glib):
    p = glib.root / "library.toml"
    p.write_text(p.read_text().replace("track_text = false", "track_text = true"))
    lib = Library(glib.root)
    assert lib.config["library"]["track_text"] is True
    conf = lib.create_item("doe2026plans", BIB.replace("smith2021cold", "doe2026plans"),
                           sharing_level="confidential")
    conf.text_path.write_text("Secret.")
    bad = G.problems(lib, ["papers/smith2021cold/text.txt", "papers/doe2026plans/text.txt"])
    assert bad == ["papers/doe2026plans/text.txt: doe2026plans is confidential"]
    assert G.sync_gitignore(lib)                                         # the text rule goes
    assert "papers/*/text.txt" not in (lib.root / ".gitignore").read_text()
    assert not G.sync_gitignore(lib)                                     # idempotent


def test_git_setup_reports_tracked_text_in_an_old_library(glib, capsys):
    p = glib.root / "library.toml"
    p.write_text(p.read_text().replace("track_text = false", "track_text = true"))
    G.sync_gitignore(Library(glib.root))
    git(glib.root, "add", "-A")
    git(glib.root, "commit", "-q", "-m", "old style", "--no-verify")
    p.write_text(p.read_text().replace("track_text = true", "track_text = false"))
    assert cli.main(["-L", str(glib.root), "git-setup"]) == 1
    out = capsys.readouterr().out
    assert "papers/smith2021cold/text.txt" in out and "filter-repo" in out
    assert cli.main(["-L", str(glib.root), "git-check", "--all"]) == 1
    assert "already runs" in G.install_hook(Library(glib.root))


def test_model_jobs_and_replies_are_ignored(glib):
    for p in ("derived/batches/b1/jobs.jsonl", "derived/paper-eval/r/results.jsonl", "derived/model-eval/r/x.jsonl"):
        f = glib.root / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("paper text")
    git(glib.root, "add", "-A")
    assert not [p for p in G.staged(glib.root) if p.startswith("derived/")]
