import json

import numpy as np
from test_localmodel import strict
from test_search import ilib  # noqa: F401

from litnav import sharing as S, topics


def test_jobs_open_only_and_no_topic_list(ilib):
    lib = strict(ilib)
    rd = topics.new_run(lib)
    js = [json.loads(l) for l in (rd / "jobs.jsonl").read_text().splitlines()]
    assert js and all(lib.get(j["key"]).allowed(S.SEND_OUT) for j in js)
    assert "convection" not in topics.SYSTEM.lower() and "mcs" not in topics.SYSTEM.lower()


def test_consensus_finds_separated_groups_without_a_k():
    rng = np.random.default_rng(0)
    centres = np.eye(8)[:3]
    m = np.vstack([c + 0.05 * rng.standard_normal((30, 8)) for c in centres]).astype("float32")
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    lab = topics.consensus(m, sweep=(2, 3, 4, 6), seeds=(0, 1))
    assert len(set(lab[:30])) == 1 and len(set(lab[30:60])) == 1 and len({lab[0], lab[30], lab[60]}) == 3


class FakeEmb:
    name = "fake"

    def docs(self, texts):
        return np.array([[1.0, 0.0] if "track" in t else [0.0, 1.0] for t in texts], dtype="float32")


def test_cluster_keeps_recurring_topics(tmp_path):
    rows = [{"id": "topics-a", "ok": True, "output": {"aspects": [{"aspect": "storm tracking", "detail": "d", "sections": []},
                                                                  {"aspect": "soil moisture", "detail": "d", "sections": []}]}},
            {"id": "topics-b", "ok": True, "output": {"aspects": [{"aspect": "cell tracking", "detail": "d", "sections": []}]}}]
    (tmp_path / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    c = topics.cluster(None, tmp_path, FakeEmb())
    assert [x["keys"] for x in c["clusters"]] == [["a", "b"]] and c["singletons"] == 1
