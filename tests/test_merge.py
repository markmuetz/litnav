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
