import datetime as dt

import pytest

from litnav import keys


@pytest.mark.parametrize("sur,year,title,expect", [
    ("Smith", 2021, "Cold pools and MCS propagation", "smith2021cold"),
    # leading function words skipped
    ("Jones", 2019, "What is the role of cold pools?", "jones2019role"),
    ("Jones", 2019, "On the dynamics of squall lines", "jones2019dynamics"),
    ("Jones", 2019, "How do updrafts entrain?", "jones2019updrafts"),
    ("Jones", 2019, "Can we use 1D models?", "jones2019use"),
    # `toward` is deliberately a content word
    ("Khouider", 2025, "Toward a theory of tropical waves", "khouider2025toward"),
    # accents folded, not deleted
    ("Volonté", 2021, "Interaction of convection", "volonte2021interaction"),
    ("Wåhlström", 2010, "Gravity waves", "wahlstrom2010gravity"),
    (r"Schr{\"o}dinger", 1926, "Quantisation", "schrodinger1926quantisation"),
    # particles kept as part of the surname
    ("van den Heever", 2021, "Colorado convection", "vandenheever2021colorado"),
    # Part I/II get a digit
    ("Betts", 1986, "A new convective adjustment scheme. Part I: Observational", "betts1986new1"),
    ("Grandpeix", 2010, "A density current parameterization. Part II", "grandpeix2010density2"),
])
def test_derive(sur, year, title, expect):
    assert keys.derive(sur, year, title) == expect


def test_marker():
    assert keys.derive("Smith", 2021, "Cold pools", marker="preprint") == "smith2021cold_preprint"


@pytest.mark.parametrize("y,ok", [(1799, False), (1800, True), (2888, False), ("0308", False),
                                  (dt.date.today().year + 1, True), ("abcd", False)])
def test_plausible_year(y, ok):
    assert keys.plausible_year(y) is ok


def test_validate_rejects_article_numbers():
    # grl28889 would parse as grl + 2888 + 9 without the plausible-year check
    assert keys.validate("grl28889")
    assert keys.validate("nature03089")
    assert keys.validate("smith2021cold") == []
    assert keys.validate("ipcc2021wg1ch01") == []
    assert keys.validate("hersbach2020era5") == []
    assert keys.validate("smith2021cold_preprint") == []
    assert keys.validate("smith2021cold_bogus")


def test_collision_appends_next_word():
    taken = {"smith2021cold"}
    ex = lambda k: k.casefold() in taken
    assert keys.derive_unique("Smith", 2021, "Cold pools and MCS propagation", ex) == "smith2021coldpools"
    taken.add("smith2021coldpools")
    assert keys.derive_unique("Smith", 2021, "Cold pools and MCS propagation", ex) == "smith2021coldpoolsmcs"


def test_collision_is_case_insensitive():
    taken = {"huffman2020imerg"}
    ex = lambda k: k.casefold() in taken
    assert keys.derive_unique("Huffman", 2020, "IMERG version 6", ex) != "huffman2020imerg"


def test_collision_never_numeric():
    taken = {"smith2021cold"}
    ex = lambda k: k.casefold() in taken
    k = keys.derive_unique("Smith", 2021, "Cold", ex)
    assert k == "smith2021coldb"


def test_surname_forms():
    assert keys.surname("Smith, Jane A and Jones, B") == "Smith"
    assert keys.surname("Jane A Smith and B Jones") == "Smith"
    assert keys.surname("{van den Heever}, Susan") == "van den Heever"


def test_plain():
    assert keys.plain(r"Cold pools and {MCS}s") == "Cold pools and MCSs"
    assert keys.plain(r"M{\"u}ller") == "Müller"
