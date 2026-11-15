import pytest

from litnav import refs, rename

from conftest import BIB


def keys_of(t):
    return [r.key for r in refs.find(t)]


def test_find_forms():
    t = "See [@smith2021cold, sec. 4] and [@a2020x; @b2021y]. Also @c2019z showed it."
    assert keys_of(t) == ["smith2021cold", "a2020x", "b2021y", "c2019z"]


def test_find_skips_email_and_code():
    t = "mail me@example.com; `@notakey` and\n```\n@alsonot\n```\nbut [@yes2020x]."
    assert keys_of(t) == ["yes2020x"]


def test_exact_keys():
    t = "[@stein2014threedimensional]"
    assert keys_of(t) == ["stein2014threedimensional"]
    new, n = refs.rewrite(t, "stein2014three", "x2014y")
    assert n == 0 and new == t


def test_convert_backticks():
    known = {"smith2021cold": "smith2021cold", "smc2021": "smith2021cold"}
    r = lambda k: known.get(k.casefold())
    t = "Cf. `smith2021cold` and `SMC2021`, not `litnav index`.\n```\n`smith2021cold`\n```\n"
    new, n = refs.convert_backticks(t, r)
    assert n == 2
    assert new.startswith("Cf. [@smith2021cold] and [@smith2021cold], not `litnav index`.")
    assert "```\n`smith2021cold`\n```" in new


def test_rename_end_to_end(lib):
    it = lib.create_item("TMM1982", BIB, tags=["x"])
    lib.pdf_dir.mkdir(parents=True, exist_ok=True)
    it.pdf_path.write_bytes(b"%PDF")
    lib.variants.mkdir(parents=True, exist_ok=True)
    (lib.variants / "TMM1982.2026-01-01.pdf").write_bytes(b"%PDF v")
    note = lib.root / "notes" / "n.md"
    note.write_text("As [@TMM1982] showed; not [@TMM19820].\n")
    other = lib.create_item("b2020x", BIB)
    other.meta["relations"] = {"preprint_of": "TMM1982"}
    other.save_meta()
    from litnav import projects
    (lib.root / "projects" / "P").mkdir()
    projects.save(lib, "P", [{"key": "TMM1982"}])
    p = rename.plan(lib, "TMM1982", "thorpe1982twodimensional")
    rename.apply(lib, p)
    n = lib.get("thorpe1982twodimensional")
    assert n.has_pdf and n.entry.key == "thorpe1982twodimensional"
    assert lib.get("TMM1982").key == "thorpe1982twodimensional"      # alias
    assert note.read_text() == "As [@thorpe1982twodimensional] showed; not [@TMM19820].\n"
    assert (lib.variants / "thorpe1982twodimensional.2026-01-01.pdf").exists()
    assert projects.project_keys(lib, "P") == ["thorpe1982twodimensional"]
    assert lib.get("b2020x").meta["relations"]["preprint_of"] == "thorpe1982twodimensional"
    assert refs.check(lib) == [(note, refs.find(note.read_text())[1])]


def test_rename_refuses_collision(lib):
    lib.create_item("a2020x", BIB)
    lib.create_item("b2020x", BIB)
    from litnav.library import KeyError_
    with pytest.raises(KeyError_):
        rename.plan(lib, "a2020x", "B2020X")


def test_case_only_rename(lib):
    lib.create_item("huffman2020imerg", BIB)
    rename.apply(lib, rename.plan(lib, "huffman2020imerg", "huffman2020IMERG"))
    assert lib.keys() == ["huffman2020IMERG"]
    assert lib.get("huffman2020IMERG").meta.get("aliases", []) == []
