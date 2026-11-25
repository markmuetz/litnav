import json

import numpy as np
from test_localmodel import strict
from test_search import ilib  # noqa: F401

from litnav import sharing as S, templates as T


class FakeEmb:
    name = "fake"

    def docs(self, texts):
        return np.array([[1.0, 0.0] if "threshold" in t else [0.0, 1.0] for t in texts], dtype="float32")


def _topics_run(tmp, keys):
    (tmp / "topics.json").write_text(json.dumps({"topics": [
        {"name": "Cloud tracking algorithms", "description": "d", "kind": "method", "clusters": ["c001"]}]}))
    (tmp / "clusters.json").write_text(json.dumps({"clusters": [{"id": "c001", "keys": keys, "sample": ["tracking"]}]}))
    (tmp / "results.jsonl").write_text("".join(json.dumps({"id": f"topics-{k}", "ok": True, "output": {
        "aspects": [{"aspect": "tracking", "detail": "d", "sections": []}]}}) + "\n" for k in keys))


def test_route_inventory_tally_and_t1(ilib, tmp_path):
    lib = strict(ilib)
    keys = sorted(it.key for it in lib.items())
    _topics_run(tmp_path, keys)
    rd, r = T.route(lib, tmp_path, "tracking")
    assert r["papers"] == ["jones2019gravity"]                         # open papers only
    js = T.inventory_jobs(lib, rd)
    assert [j["key"] for j in js] == ["jones2019gravity"] and "Cloud tracking algorithms" in js[0]["messages"][1]["content"]
    res = [{"id": "t0inv-jones2019gravity", "ok": True, "output": {"reports_topic": True, "details": [
        {"detail": "brightness temperature threshold", "value_given": True, "example": "241 K", "sections": []},
        {"detail": "minimum lifetime", "value_given": False, "example": "", "sections": []}]}}]
    (rd / "results.jsonl").write_text("".join(json.dumps(x) + "\n" for x in res))
    t = T.tally(lib, rd, FakeEmb())
    assert t["papers_inventoried"] == 1 and len(t["kinds"]) == 2
    tpl = {"fields": [{"name": "tb_threshold", "definition": "Tb threshold, e.g. 241 K or 225 K", "applies_to": "experiment",
                       "form": "as_written", "units": "K", "allowed_values": [], "where_found": "methods",
                       "inventories": [1]},
                      {"name": "dataset", "definition": "d", "applies_to": "paper", "form": "as_written", "units": "",
                       "allowed_values": [], "where_found": "", "inventories": []}],
           "other_details": "", "notes": ""}
    T._save(rd, tpl, "m", "draft-1", T.evidence(rd))
    d = json.loads((rd / "draft-1.json").read_text())["template"]["fields"]
    assert d[0]["papers"] == ["jones2019gravity"] and d[0]["numbers_not_in_evidence"] == ["225"]
    out = T.t1_jobs(lib, rd)
    j = json.loads((out / "jobs.jsonl").read_text().splitlines()[0])
    sch = j["schema"]["properties"]
    assert sch["values"]["items"]["properties"]["field"]["enum"] == ["tb_threshold", "dataset"]
    pid = j["ids"][0]
    (out / "results.jsonl").write_text(json.dumps({"id": j["id"], "ok": True, "output": {
        "reports_topic": True, "experiments": ["CTL"], "other": "", "values": [
            {"experiment": "CTL", "field": "tb_threshold", "value": "241 K", "passage": pid},
            {"experiment": "", "field": "dataset", "value": "GPM", "passage": "made-up"}]}}) + "\n")
    s = T.t1_summary(rd)
    assert s["values"] == 2 and s["passages_not_in_paper"] == 1 and s["papers_stating"]["tb_threshold"] == 1
    assert lib.get(j["key"]).allowed(S.SEND_OUT)
