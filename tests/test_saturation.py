from litnav import saturation as SAT


def test_curve_flattens_when_papers_repeat_kinds():
    rows = [(f"p{i}", "x") for i in range(10)] + [(f"p{i}", "y") for i in range(10)] + [("p0", "z")]
    labels = [0] * 10 + [1] * 10 + [2]
    c = SAT.curve(rows, labels, orders=10)
    assert c["papers"] == 10 and c["kinds"] == 3 and c["recurring"] == 2
    assert c["recurring_curve"][0]["mean"] == 2 and c["recurring_curve"][-1]["mean"] == 2
    assert c["new_recurring_per_paper_last_fifth"] == 0
    assert c["all"][-1]["mean"] == 3


def test_curve_keeps_rising_when_every_paper_adds_kinds():
    rows = [(f"p{i}", f"k{i}") for i in range(10)]
    c = SAT.curve(rows, list(range(10)), orders=10)
    assert c["new_per_paper_last_fifth"] == 1 and c["recurring"] == 0
