from litnav import evaluate

from test_search import ilib, FakeEmbedder  # noqa: F401


def test_eval_scores(ilib):
    (ilib.root / "eval" / "questions.toml").write_text('''
[[question]]
q = "cold pools"
keys = ["smith2021cold"]
passages = ["Cold   pools"]
kind = "exact-name"

[[question]]
q = "WSR-88D gravity waves"
keys = ["jones2019gravity"]
kind = "exact-name"
''')
    res = evaluate.run(ilib, None)
    s = res["summary"]
    assert set(s) == {"keyword/passage", "keyword/paper"}
    assert s["keyword/paper"]["all"]["recall"] == 1.0
    assert s["keyword/paper"]["all"]["n"] == 2
    assert "exact-name" in s["keyword/paper"]
    b = dict(res, rows=[dict(r, **{"keyword/paper": {"recall": 0, "mrr": 0}}) for r in res["rows"]])
    out = evaluate.compare(res, b)
    assert "0 /   0 /   2" in out


def test_score_one_and_fold():
    q = {"keys": ["a", "b"], "passages": ["hyphenated text"]}
    res = [{"key": "c", "text": ""}, {"key": "a", "text": "some Hyphen-\nated  TEXT here"}]
    assert evaluate.score_one(res, q, "passage") == {"recall": 0.5, "mrr": 0.5}
    assert evaluate.score_one(res, q, "paper")["mrr"] == 0.5
    assert evaluate.sign_test(10, 0) < 0.01 and evaluate.sign_test(3, 3) == 1.0
