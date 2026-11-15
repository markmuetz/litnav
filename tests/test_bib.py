from litnav import bib

CROSSREF = ('@article{Smith_2021, title={Cold pools: the {MJO} link}, volume={78}, '
            'ISSN={0022-4928}, url={http://dx.doi.org/10.1175/JAS-D-20-0001.1}, '
            'DOI={10.1175/JAS-D-20-0001.1}, number={3}, journal={Journal of the Atmospheric '
            'Sciences}, publisher={American Meteorological Society}, author={Smith, J. A. and '
            'Jones, TN}, year={2021}, month=mar, pages={123–145} }')


def test_crossref_to_house_style():
    out = bib.normalise(CROSSREF)
    assert out.splitlines()[0] == "@article{Smith_2021,"
    lines = [l.strip().split(" = ")[0] for l in out.splitlines()[1:-1]]
    assert lines[0] == "title" and lines[1] == "author"
    assert lines[-2:] == ["url", "doi"]
    assert "    author = {Smith, J A and Jones, T N}," in out
    assert "    month = mar," in out
    assert "doi = {10.1175/jas-d-20-0001.1}" in out
    assert "ISSN" not in out and "issn = " in out


def test_idempotent():
    once = bib.normalise(CROSSREF)
    assert bib.normalise(once) == once


def test_quoted_braced_becomes_double_braced():
    # "{X}" -> {{X}}, never {X}: only content braces protect capitals
    out = bib.normalise('@article{k, title = "{MJO}"}')
    assert "title = {{MJO}}" in out
    out = bib.normalise('@article{k, title = "Plain"}')
    assert "title = {Plain}" in out


def test_all_caps_names_not_split():
    out = bib.normalise("@article{k, author = {BASTIN, MARK E. and Smith, AB}}")
    assert "BASTIN, MARK E" in out
    assert "M A R K" not in out
    assert "Smith, A B" in out


def test_hyphenated_initials():
    out = bib.normalise("@article{k, author = {Lafore, J.-P.}}")
    assert "Lafore, J-P" in out


def test_bare_month_macro():
    assert "month = jun" in bib.normalise("@article{k, month = June}")
    assert "month = jan" in bib.normalise("@article{k, month = jan}")


def test_preamble_kept():
    out = bib.normalise("% from the AMS site\n@article{k, title = {T}}")
    assert out.startswith("% from the AMS site\n@article{k,")


def test_unparseable_returns_none():
    assert bib.normalise("not a bib") is None
    assert bib.normalise("@article{k, title = {unclosed") is None


def test_parse_many():
    es = bib.parse_many("@string{x = {y}}\n@article{a, title={A}}\n@book{b, title={B {C}}}\n")
    assert [e.key for e in es] == ["a", "b"]
    assert es[1].get("title") == "B {C}"


def test_authority(tmp_path):
    (tmp_path / "author-canonical.tsv").write_text(
        "surname\tinitial\tcanonical\tflag\nsmith\tj\tSmith, Jane A\t\nlee\tj\t\tsplit\n")
    (tmp_path / "journal-canonical.tsv").write_text(
        "canonical\tvariants\nJournal of the Atmospheric Sciences\tJ. Atmos. Sci.|JAS\n")
    a = bib.Authority.load(tmp_path)
    out = bib.normalise("@article{k, author = {Smith, J. and Lee, J. K.}, journal = {J Atmos Sci}}", a)
    assert "Smith, Jane A and Lee, J K" in out
    assert "journal = {Journal of the Atmospheric Sciences}" in out
