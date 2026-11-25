import json

from test_localmodel import strict
from test_search import ilib  # noqa: F401

from litnav import modelreg, sharing as S


def test_family():
    assert modelreg.norm_family("CAM6") == "cam" and modelreg.norm_family("WRF v4.1") == "wrf"


def test_build_lookup_and_scoring(tmp_path):
    m = lambda fam, dx, dc, p, cfg="", label="": {"name": fam, "family": fam, "configuration": cfg,
                                                 "role": "run_in_this_paper", "horizontal_grid_spacing": dx,
                                                 "deep_convection": dc, "convection_passage": p, "author_label": label}
    rows = [
        {"id": "modelreg-a2020", "ok": True, "output": {"models": [m("CAM6", "1 degree", "parametrized", "a#1"),
                                                                   m("SAM", "1 km", "explicit", "a#2")],
                                                        "definitions": [{"term": "large-eddy simulation",
                                                                         "meaning": "m", "grid_spacing": "100 m",
                                                                         "passage": "a#3"}]}},
        {"id": "modelreg-b2021", "ok": True, "output": {"models": [m("SAM", "2 km", "explicit", "b#1"),
                                                                   m("CAM", "", "not_stated", "")],
                                                        "definitions": [{"term": "convection-permitting",
                                                                         "meaning": "m", "grid_spacing": "4 km",
                                                                         "passage": "b#2"}]}},
        {"id": "modelreg-c2022", "ok": True, "output": {"models": [m("SAM", "1.5 km", "parametrized", "c#1")],
                                                        "definitions": []}}]
    (tmp_path / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "run.json").write_text('{"prompt_version": 2}')
    reg = modelreg.build(tmp_path)
    assert len(reg["cases"]) == 5 and len(reg["definitions"]) == 2
    assert modelreg.lookup(reg, "Community Atmosphere Model version 6 (CAM6-FV)", "")["treatment"] == "parametrized"
    assert modelreg.lookup(reg, "SAM", "1 km") is None                          # c2022 disagrees: no rule
    assert modelreg.lookup(reg, "SAM", "1 km", exclude="c2022")["keys"] == ["a2020", "b2021"]
    assert modelreg.lookup(reg, "SAM", "50 km", exclude="c2022") is None        # no neighbour within a factor 2
    assert modelreg.lookup(reg, "SAMURAI", "1 km") is None                      # whole-word family match
    assert modelreg.explicit_class(reg, "", 50.0) == "les"                      # from the definitions
    assert modelreg.explicit_class(reg, "", 2000.0) == "cpm"
    assert modelreg.explicit_class(reg, "", 8000.0) == "explicit_model"         # beyond every definition
    assert modelreg.explicit_class(None, "a convection-permitting run", None) == "cpm"   # the authors' label
    sc = modelreg.score_stated(reg)
    assert sc["stated_cases"] == 4 and sc["answered"] >= 1


def test_jobs_open_only(ilib):
    lib = strict(ilib)
    rd = modelreg.new_run(lib)
    keys = [json.loads(l)["key"] for l in (rd / "jobs.jsonl").read_text().splitlines()]
    assert keys == ["jones2019gravity"] and all(lib.get(k).allowed(S.SEND_OUT) for k in keys)
