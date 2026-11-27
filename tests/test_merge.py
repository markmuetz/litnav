import json

import numpy as np

from litnav import merge


class Fake:
    name = "fake"

    def docs(self, texts):
        return np.array([[1.0, 0.0] if "rain" in t else [0.0, 1.0] for t in texts], dtype="float32")


def run(tmp_path):
    rd = tmp_path / "g1"
    rd.mkdir()
    ms = {"a": ["more rain", "less wind"], "b": ["rain grows", "wind drops"]}
    js, rs = [], []
    for k, stmts in ms.items():
        js.append({"id": f"g1-{k}--g01", "key": k})
        rs.append({"id": f"g1-{k}--g01", "ok": True, "output": {"mentions": [{"statement": s} for s in stmts]}})
    (rd / "jobs.jsonl").write_text("".join(json.dumps(j) + "\n" for j in js))
    (rd / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rs))
    (rd / "run.json").write_text("{}")
    return rd


def test_candidates_come_from_other_papers_nearest_first(tmp_path):
    rd = run(tmp_path)
    merge.candidates(rd, Fake(), k=2)
    near = {r["id"]: r["near"] for r in map(json.loads, (rd / "candidates.jsonl").read_text().splitlines())}
    assert near["a#m1"][0][0] == "b#m1" and all(x.startswith("b#") for x, _ in near["a#m1"])
    assert merge.recall(rd, [("a#m1", "b#m1"), ("a#m2", "b#m1")], ks=(1, 2)) == {"pairs": 2, "at_1": 0.5, "at_2": 1.0}


class Lib:
    class It:
        title = "T"

        def allowed(self, purpose):
            return True

    def get(self, key):
        return self.It()


def test_each_pair_is_asked_once_and_answers_map_back(tmp_path):
    rd = run(tmp_path)
    merge.candidates(rd, Fake(), k=2)
    js = [json.loads(l) for l in merge.pair_jobs(Lib(), rd, k=2).read_text().splitlines()]
    seen = [frozenset((j["a"], c)) for j in js for c in j["candidates"]]
    assert len(seen) == len(set(seen)) == 4                          # 2 x 2 cross-paper pairs
    j = js[0]
    (rd / "pairs-results.jsonl").write_text(json.dumps({"id": j["id"], "ok": True, "output": {"pairs": [
        {"n": 1, "verdict": "same", "reason": "r"}, {"n": 9, "verdict": "same", "reason": "bad n"},
        {"n": 2, "starts": "something else entirely", "verdict": "same", "reason": "misaligned"}]}}) + "\n")
    assert merge.pairs(rd) == [{"a": j["a"], "b": j["candidates"][0], "verdict": "same", "reason": "r", "rank": 1}]
