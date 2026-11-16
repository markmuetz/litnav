"""project, draft and import --from-downloads."""
import os
import time

import pytest

from litnav import drafts, importer, projects
from litnav.cli import main

from conftest import BIB
from test_search import ilib  # noqa: F401


def test_project_commands(lib):
    lib.create_item("smith2021cold", BIB)
    with pytest.raises(ValueError):
        projects.add(lib, "P", ["smith2021cold"])
    assert main(["-L", str(lib.root), "project", "add", "P", "smith2021cold", "--create",
                 "--status", "to read", "-t", "cp"]) == 0
    assert main(["-L", str(lib.root), "project", "set", "P", "smith2021cold", "--status", "read",
                 "-t", "x", "--untag", "cp", "--note", "good"]) == 0
    p = projects.load(lib, "P")[0]
    assert (p["status"], p["tags"], p["note"]) == ("read", ["x"], "good")
    assert main(["-L", str(lib.root), "project", "rm", "P", "smith2021cold"]) == 0
    assert projects.load(lib, "P") == []



def test_draft_version(lib, tmp_path):
    it = lib.create_item("doe2026plans_draft", None, tags=["coauthor_draft"],
                         sharing_level="confidential")
    it.pdf_path.parent.mkdir(parents=True, exist_ok=True)
    it.pdf_path.write_bytes(b"%PDF v1")
    new = tmp_path / "v2.pdf"
    new.write_bytes(b"%PDF v2")
    p = drafts.plan_version(lib, "doe2026plans_draft", new, received="2026-11-16", label="r1")
    assert new.exists() and it.pdf_path.read_bytes() == b"%PDF v1"     # plan changes nothing
    drafts.apply(lib, p)
    assert it.pdf_path.read_bytes() == b"%PDF v2" and not new.exists()
    old = list(lib.variants.glob("doe2026plans_draft.*-r1.pdf"))
    assert len(old) == 1 and old[0].read_bytes() == b"%PDF v1"
    assert drafts.load(lib)[-1]["received"] == "2026-11-16"
    assert not it.text_path.exists()
    other = lib.create_item("smith2021cold", BIB)
    with pytest.raises(ValueError):
        drafts.plan_version(lib, other.key, tmp_path / "v2.pdf")


def test_downloads_dedupe_and_skip(lib, tmp_path):
    d = tmp_path / "dl"
    d.mkdir()
    (d / "paper.pdf").write_bytes(b"%PDF a")
    (d / "paper-2.pdf").write_bytes(b"%PDF a")
    (d / "paper (1).pdf").write_bytes(b"%PDF a")
    (d / "other-2.pdf").write_bytes(b"%PDF b")
    (d / "GSRM_draft_v2.pdf").write_bytes(b"%PDF c")
    (d / "old.pdf").write_bytes(b"%PDF d")
    t = time.time() - 5 * 3600
    os.utime(d / "old.pdf", (t, t))
    got = {p.name for p in importer.downloads(lib, folder=d, log=lambda *a: None)}
    assert got == {"paper.pdf", "other-2.pdf"}


def test_project_filter_in_tools(ilib):
    from litnav import sharing as S
    from litnav.tools import Tools
    projects.add(ilib, "P", ["jones2019gravity"], create=True)
    t = Tools(ilib, purpose=S.SEND_OUT, embedder=None)
    assert {r["key"] for r in t.list_papers(project="P")} == {"jones2019gravity"}
    hits = t.search_literature("cold pools gravity", k=25, mode="keyword", project="P")
    assert hits and {h["key"] for h in hits} == {"jones2019gravity"}
