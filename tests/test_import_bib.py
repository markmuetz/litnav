from litnav.importers import bibtex, legacy


def test_file_field_forms():
    assert bibtex.file_paths("Full Text PDF:/home/u/Zotero/storage/AB/x.pdf:application/pdf") == \
        ["/home/u/Zotero/storage/AB/x.pdf"]
    assert bibtex.file_paths(r"Full Text:C\:\\Users\\u\\x.pdf:application/pdf;Snapshot:s.html:text/html") == \
        [r"C:\Users\u\x.pdf", "s.html"]
    assert bibtex.file_paths(":papers/x.pdf:PDF") == ["papers/x.pdf"]


def test_import_bib_keeps_old_key_as_alias(lib, tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    b = tmp_path / "my.bib"
    b.write_text("""@article{smithColdPools2021,
  title = {Cold Pools and {MCS} Propagation},
  author = {Smith, Jane and Jones, Bob},
  year = {2021}, doi = {10.1/X},
  file = {Full Text PDF:%s:application/pdf},
  keywords = {convection, cold pools}
}
@book{doe1990book, title = {A Book}, author = {Doe, John}, year = {1990}}
""" % pdf)
    rows = bibtex.plan(lib, b, log=lambda *a: None)
    assert [r["key"] for r in rows] == ["smith2021cold", "doe1990book"]
    bibtex.apply(lib, rows, keep_keywords_as_tags=True, log=lambda *a: None)
    it = lib.get("smithColdPools2021")
    assert it.key == "smith2021cold" and it.has_pdf
    assert it.tags == ["convection", "cold_pools"]
    assert "file =" not in it.bib_path.read_text()
    assert lib.get("doe1990book").meta["files"]["pdf"] is False
    # importing again finds everything held
    rows = bibtex.plan(lib, b, log=lambda *a: None)
    assert [r["status"] for r in rows] == ["held", "held"]


def test_legacy_convert(lib, tmp_path):
    src = tmp_path / "old"
    lit = src / "literature"
    store = tmp_path / "Lit"
    store.mkdir()
    for k, tags in [("smith2021cold", "UPFLO,bulk_imported\n"), ("doe2026x_draft", "coauthor_draft,confidential\n"),
                    ("book1990x", "physical_copy")]:
        d = lit / k
        d.mkdir(parents=True)
        (d / "ref.bib").write_text("@article{%s, title = {T}, year = {2021}}\n" % k)
        (d / "tags.txt").write_text(tags)
        if k != "book1990x":
            (store / f"{k}.pdf").write_bytes(b"%PDF " + k.encode())
            (d / f"{k}.pdf").symlink_to(store / f"{k}.pdf")
            (d / "extracted_text.txt").write_text("text of " + k)
    (lit / "smith2021cold" / "summary.json").write_text('{"summary": "s"}')
    (src / "projects" / "UPFLO").mkdir(parents=True)
    (src / "projects" / "UPFLO" / "smith2021cold").symlink_to(lit / "smith2021cold")
    c = legacy.convert(lib, src, apply=True, log=lambda *a: None)
    assert c["items"] == 3
    a = lib.get("smith2021cold")
    assert a.tags == ["UPFLO", "bulk_imported"] and a.has_pdf and a.has_text
    assert a.summary()["summary"] == "s"
    d = lib.get("doe2026x_draft")
    assert d.sharing == "confidential" and not d.has_text      # text never carried over
    assert lib.get("book1990x").meta["files"]["pdf"] is False
    from litnav import projects
    assert projects.project_keys(lib, "UPFLO") == ["smith2021cold"]


def test_legacy_pdf_in_subfolder_used_in_place(lib, tmp_path):
    # a PDF already inside pdf_dir, but in a subfolder, must not be copied
    sub = lib.pdf_dir / "IPCC"
    sub.mkdir(parents=True)
    (sub / "ipcc2021wg1ch01.pdf").write_bytes(b"%PDF ipcc")
    d = tmp_path / "old" / "literature" / "ipcc2021wg1ch01"
    d.mkdir(parents=True)
    (d / "ipcc2021wg1ch01.pdf").symlink_to(sub / "ipcc2021wg1ch01.pdf")
    legacy.convert(lib, tmp_path / "old", apply=True, log=lambda *a: None)
    it = lib.get("ipcc2021wg1ch01")
    assert it.has_pdf and it.pdf_path == sub / "ipcc2021wg1ch01.pdf"
    assert not (lib.pdf_dir / "ipcc2021wg1ch01.pdf").exists()
