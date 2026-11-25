import json

from test_localmodel import strict
from test_search import ilib  # noqa: F401

from litnav import modelreg, sharing as S


def test_dx_class_and_family():
    assert modelreg.dx_class("2.5 km") == "km" and modelreg.dx_class("250 m") == "les"
    assert modelreg.dx_class("5 km") == "grey" and modelreg.dx_class("0.9x1.25 degrees") == "coarse"
    assert modelreg.dx_class("") == "unknown"
    assert modelreg.norm_family("CAM6") == "cam" and modelreg.norm_family("WRF v4.1") == "wrf"


def test_build_and_lookup(tmp_path):
    rows = [
        {"id": "modelreg-a2020", "ok": True, "output": {"models": [
            {"family": "CAM6", "horizontal_grid_spacing": "1 degree", "deep_convection": "parametrized",
             "convection_passage": "a2020#2-1#x"},
            {"family": "SAM", "horizontal_grid_spacing": "1 km", "deep_convection": "explicit",
             "convection_passage": "a2020#3-1#y"}]}},
        {"id": "modelreg-b2021", "ok": True, "output": {"models": [
            {"family": "SAM", "horizontal_grid_spacing": "2 km", "deep_convection": "explicit",
             "convection_passage": "b2021#2-1#z"},
            {"family": "CAM", "horizontal_grid_spacing": "", "deep_convection": "not_stated",
             "convection_passage": ""}]}}]
    (tmp_path / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    reg = modelreg.build(tmp_path)
    assert modelreg.lookup(reg, "Community Atmosphere Model version 6 (CAM6-FV)", "") == ("parametrized_model", ["a2020"])
    assert modelreg.lookup(reg, "SAM", "1 km") == ("cpm", ["a2020", "b2021"])
    assert modelreg.lookup(reg, "SAMURAI", "1 km") is None          # whole-word family match
    assert modelreg.lookup(reg, "WRF", "3 km") is None


def test_jobs_open_only(ilib):
    lib = strict(ilib)
    rd = modelreg.new_run(lib)
    keys = [json.loads(l)["key"] for l in (rd / "jobs.jsonl").read_text().splitlines()]
    assert keys == ["jones2019gravity"] and all(lib.get(k).allowed(S.SEND_OUT) for k in keys)
