import pytest

from litnav import sharing as S
from litnav.library import KeyError_

from conftest import BIB


def test_create_and_read(lib):
    it = lib.create_item("smith2021cold", BIB, tags=["cold_pools"])
    it = lib.get("smith2021cold")
    assert it.title == "Cold pools and the propagation of MCSs"
    assert it.authors == ["Smith, Jane A", "Jones, B"]
    assert it.doi == "10.1175/jas-d-20-0001.1"
    assert it.tags == ["cold_pools"]
    assert it.sharing == "licensed"
    assert "{{MCS}}" not in it.bib_path.read_text()   # content braces kept as they were
    assert "{MCS}" in it.bib_path.read_text()


def test_bib_key_follows_item_key(lib):
    it = lib.create_item("other2021key", BIB)
    assert it.entry.key == "other2021key"


def test_case_insensitive_uniqueness(lib):
    lib.create_item("huffman2020IMERG", BIB)
    with pytest.raises(KeyError_):
        lib.create_item("huffman2020imerg", BIB)
    assert lib.get("HUFFMAN2020imerg").key == "huffman2020IMERG"


def test_unsafe_keys_rejected(lib):
    for k in ("a/b", "a:b", "a b", ""):
        with pytest.raises(KeyError_):
            lib.create_item(k)


def test_aliases_resolve(lib):
    it = lib.create_item("smith2021cold", BIB)
    it.meta["aliases"] = ["SMC2021"]
    it.save_meta()
    lib.invalidate()
    assert lib.get("smc2021").key == "smith2021cold"
    assert lib.exists("SMC2021")


def test_items_filtered_by_purpose(lib):
    lib.create_item("a2020x", BIB)
    lib.create_item("b2020x", BIB, sharing_level="confidential")
    lib.create_item("c2020x", BIB, sharing_level="open")
    keys = lambda p: [i.key for i in lib.items(p)]
    assert keys(S.LOCAL) == ["a2020x", "b2020x", "c2020x"]
    assert keys(S.SEND_OUT) == ["a2020x", "c2020x"]
    assert keys(S.LOOKUP) == ["a2020x", "c2020x"]
    assert keys(S.SERVE_TEXT) == ["c2020x"]


def test_tags(lib):
    it = lib.create_item("a2020x", BIB)
    it.add_tag("x")
    it.add_tag("x")
    assert lib.get("a2020x").tags == ["x"]
    with pytest.raises(ValueError):
        it.add_tag("two words")
    it.remove_tag("x")
    assert lib.get("a2020x").tags == []


def test_meta_roundtrip_is_tidy(lib):
    it = lib.create_item("a2020x", BIB)
    txt = it.meta_path.read_text()
    assert "aliases" not in txt and "[ids]" not in txt   # empties dropped
    assert "added" in txt
