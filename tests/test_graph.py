from litnav import graph


def m(i, key, t="shown", rests=("CTL",)):
    return {"id": i, "key": key, "statement": i, "claim_type": t, "rests_on": list(rests)}


def test_a_group_never_holds_a_pair_judged_otherwise():
    ms = [m("a#m1", "a"), m("b#m1", "b"), m("c#m1", "c")]
    ps = [{"a": "a#m1", "b": "b#m1", "verdict": "same"}, {"a": "b#m1", "b": "c#m1", "verdict": "same"},
          {"a": "a#m1", "b": "c#m1", "verdict": "contradicts"}]
    sim = {frozenset(("a#m1", "b#m1")): 0.9, frozenset(("b#m1", "c#m1")): 0.8}
    cl = graph.claims(ms, ps, sim)
    assert sorted(map(len, cl.values())) == [1, 2] and ["a#m1", "b#m1"] in cl.values()
    ps[2]["verdict"] = "different"                                   # any other verdict also blocks a chain
    assert sorted(map(len, graph.claims(ms, ps, sim).values())) == [1, 2]


def test_same_model_family_is_one_root_and_a_scheme_is_partial():
    e = lambda k, roots, kind="realistic_simulation": {"_key": k, "_id": f"{k}|CTL", "evidence_kind": kind,
                                                        "shared_roots": roots}
    exps = [e("a", [{"kind": "model_code", "name": "WRF-ARW v3.9.1.1"}, {"kind": "physics_scheme", "name": "KF"}]),
            e("b", [{"kind": "model_code", "name": "WRF version 3.7.1"}]),
            e("c", [{"kind": "model_code", "name": "ICON"}, {"kind": "physics_scheme", "name": "KF"}])]
    assert graph.family("WRF-ARW v3.9.1.1") == graph.family("WRF version 3.7.1") == "wrf arw" or \
        graph.family("WRF version 3.7.1") == "wrf"
    rg, part = graph.roots(exps, {"a": {"smith"}, "c": {"smith"}}, {"wrf-arw v3.9.1.1": "wrf", "wrf version 3.7.1": "wrf"})
    assert len(rg) == 2 and {p["kind"] for p in part} == {"physics_scheme", "authors"}
    assert graph.status(2, 1, False) == "one line" and graph.status(3, 2, True) == "contested (multiple lines)"
    assert graph.status(0, 0, False) == "asserted"
