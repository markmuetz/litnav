"""Intervals and paired tests for the model evals."""
from litnav import evalstats as E
from litnav import modeleval


def test_wilson():
    assert E.wilson(0, 0) is None
    lo, hi = E.wilson(47, 54)
    assert lo < 47 / 54 < hi and 0.74 < lo < 0.77 and 0.93 < hi < 0.95
    assert E.wilson(10, 10)[1] == 1.0


def test_mcnemar_exact():
    assert E.mcnemar(0, 0) == 1.0
    assert E.mcnemar(5, 5) == 1.0
    assert E.mcnemar(6, 0) == round(2 / 64, 4)               # 2 * 0.5**6
    assert E.mcnemar(0, 6) == E.mcnemar(6, 0)


def test_paired_uses_shared_items_only():
    p = E.paired({"a": True, "b": False, "c": True, "x": True}, {"a": True, "b": True, "c": False, "y": False})
    assert (p["n"], p["both"], p["a_only"], p["b_only"], p["neither"]) == (3, 1, 1, 1, 0)
    assert p["a_only_ids"] == ["c"] and p["b_only_ids"] == ["b"]


def test_bootstrap():
    assert E.bootstrap_ratio([]) is None
    lo, hi = E.bootstrap_ratio([(9, 10), (5, 10), (10, 10), (7, 10)])
    assert lo <= 0.775 <= hi
    assert E.bootstrap_ratio([(3, 4)] * 5) == [0.75, 0.75]
    d = E.bootstrap_diff({"p": (1, 1), "q": (1, 2)}, {"p": (0, 1), "q": (1, 2)})
    assert d["n"] == 2 and d["diff"] == round(2 / 3 - 1 / 3, 3)


def test_modeleval_compare():
    a = [{"case": "c1", "variant": "A", "ok": True}, {"case": "c2", "variant": "A", "ok": True},
         {"case": "c1", "variant": "B", "ok": False}]
    b = [{"case": "c1", "variant": "A", "ok": False}, {"case": "c2", "variant": "A", "ok": True}]
    c = modeleval.compare(a, b)
    assert set(c) == {"A"} and c["A"]["a_only"] == 1 and c["A"]["both"] == 1
