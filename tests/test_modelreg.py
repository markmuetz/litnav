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
    hit = modelreg.lookup(reg, "SAM", "1.2 km")                    # bracketed by 1 km (a) and 1.5 km (c)
    assert hit["treatment"] is None and hit["split"] == {"parametrized": 1, "explicit": 1}
    assert modelreg.lookup(reg, "SAM", "1.5 km")["treatment"] == "parametrized"   # a stated case at that spacing
    assert modelreg.lookup(reg, "SAM", "0.5 km") is None                       # finer than any stated case
    assert modelreg.lookup(reg, "SAM", "1.8 km", exclude="c2022")["keys"] == ["a2020", "b2021"]
    assert modelreg.lookup(reg, "SAM", "50 km", exclude="c2022") is None      # beyond the stated cases
    assert modelreg.lookup(reg, "SAM", "1.8 km", exclude="c2022", match="exact") is None
    assert modelreg.lookup(reg, "SAM", "1.8 km", exclude="c2022", match=("factor", 2.0))["treatment"] == "explicit"
    assert modelreg.lookup(reg, "SAMURAI", "1 km") is None                      # whole-word family match
    sc = modelreg.score_stated(reg)
    assert sc["stated_cases"] == 4 and sc["match"] == "bracket"
    voc = [{"id": "les", "terms": ["large-eddy simulation"], "deep_convection": "explicit", "span_m": [10.0, 100.0]},
           {"id": "cp", "terms": ["convection-permitting"], "deep_convection": "explicit", "span_m": [1000.0, 4000.0]}]
    r2 = {**reg, "classes": voc}
    assert modelreg.explicit_class(r2, "", 50.0) == "les" and modelreg.explicit_class(r2, "", 2000.0) == "cp"
    assert modelreg.explicit_class(r2, "", 8000.0) == "explicit_model"         # beyond every class's span
    assert modelreg.explicit_class(r2, "our convection-permitting run", None) == "cp"   # the authors' label
    assert modelreg.explicit_class(reg, "convection-permitting", 2000.0) == "explicit_model"   # no vocabulary


def test_jobs_open_only(ilib):
    lib = strict(ilib)
    rd = modelreg.new_run(lib)
    keys = [json.loads(l)["key"] for l in (rd / "jobs.jsonl").read_text().splitlines()]
    assert keys == ["jones2019gravity"] and all(lib.get(k).allowed(S.SEND_OUT) for k in keys)


def test_v3_processes_and_senses(tmp_path):
    rows = [{"id": "modelreg-a2020", "ok": True, "output": {"models": [
        {"name": "UM", "family": "UM", "configuration": "", "role": "run_in_this_paper",
         "horizontal_grid_spacing": "1.5 km", "deep_convection": "explicit", "convection_passage": "a#1",
         "author_label": "", "processes": [
             {"process": "deep convection", "state": "explicit", "scheme": "", "passage": "a#1"},
             {"process": "turbulence", "state": "parametrized", "scheme": "Smagorinsky-Lilly", "passage": "a#2"},
             {"process": "shallow convection", "state": "partly", "scheme": "", "passage": ""}]}],
        "definitions": [{"term": "grey zone", "meaning": "m", "grid_spacing": "100 m", "process": "turbulence",
                         "passage": "a#3"}]}}]
    (tmp_path / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "run.json").write_text('{"prompt_version": 3}')
    reg = modelreg.build(tmp_path)
    assert [x["process"] for x in reg["cases"][0]["processes"]] == ["deep convection", "turbulence"]   # stated only
    assert reg["definitions"][0]["process"] == "turbulence"
    assert "processes" in modelreg.schema(["a#1"])["properties"]["models"]["items"]["required"]
    turb = {**reg, "classes": [{"id": "grey_zone_turbulence", "process": "turbulence", "terms": ["grey zone"],
                                "deep_convection": "either", "span_m": [50.0, 500.0]}]}
    assert modelreg.explicit_class(turb, "a grey zone run", 100.0) == "explicit_model"   # another process's sense
