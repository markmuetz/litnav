"""A registry of model configurations across the library: which models each paper runs or uses, at what
grid spacing, and what it *states* about deep convection (parametrized or explicit), with the passage that
says so. Built by one focused pass per paper; aggregated by model family and grid-spacing class, so that a
paper which does not state its convection treatment can take it from papers that do (an inferred label,
with its sources).

Run folders: derived/model-registry/<stamp>[-label]/ (jobs.jsonl, results.jsonl, registry.json)."""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from pathlib import Path

from . import sharing as S

PROMPT_VERSION = 2              # v2: the authors' own labels for runs, and definitions of model classes
CHARS = 200_000

KINDS = ["global_climate_model", "earth_system_model", "global_nwp_model", "global_storm_resolving_model",
         "regional_model", "cloud_resolving_model", "large_eddy_model", "single_column_model",
         "idealized_model", "machine_learning_model", "reanalysis_system", "other"]
ROLES = ["run_in_this_paper", "output_used", "described_only", "cited_only"]
DEEP = ["parametrized", "explicit", "both", "not_stated"]

SYSTEM = """You read one scientific paper and list every numerical model or model configuration in it.

For each distinct model configuration (a model at one setting: name, version, grid spacing), give:
- name: the model as the paper writes it, with version (e.g. "CAM6", "WRF v4.1", "ICON-NWP").
- family: the model code it belongs to, short and plain (e.g. "CAM", "WRF", "ICON", "IFS", "SAM", "UM"); empty
  if the paper does not make it clear.
- configuration: the paper's name for the set-up or run, if any.
- kind: one of the listed kinds.
- role: run_in_this_paper (the authors ran it), output_used (they analyse output someone else produced, e.g.
  CMIP archives), described_only, or cited_only (mentioned when discussing other work).
- horizontal_grid_spacing: as written (e.g. "2.5 km", "0.9x1.25 degrees", "T127"); empty if not given.
- deep_convection: what the paper STATES about deep convection in this configuration: parametrized (a
  deep-convection scheme is used), explicit (no deep-convection scheme; convection resolved or permitted),
  both (e.g. a scheme scaled down, or some members with and some without), or not_stated. Do not infer it
  from the grid spacing or from what you know about the model: only what this paper says.
- scheme: the convection scheme's name if stated (e.g. "Zhang-McFarlane", "Tiedtke").
- convection_passage: the id of the passage that states the convection treatment ("" if not stated).
- grid_passage: the id of the passage that gives the grid spacing ("" if not given).
- author_label: the paper's own words for what kind of model or simulation this is, as written (e.g.
  "convection-permitting", "large-eddy simulation", "cloud-resolving model", "global storm-resolving model",
  "GCM", "superparameterized"); "" if the paper gives none.

Then list definitions: every place the paper defines or characterises a class of model or a range of grid
spacing (e.g. what it calls convection-permitting, the grey zone, LES, storm-resolving, kilometre-scale):
- term: the term as written.
- meaning: what the paper says it means, in your own words.
- grid_spacing: the grid spacing or range the paper ties to it, as written ("" if none).
- passage: the id of the passage.
Only what the paper says; [] if it defines nothing.

Ensembles of many models (e.g. "13 CMIP5 GCMs") may be one entry if the paper does not list them
separately. Write in your own words; do not copy sentences. Reply with JSON only."""


def schema(ids: list[str]) -> dict:
    pid = {"type": "string", "enum": ids + [""]}
    entry = {"type": "object", "additionalProperties": False,
             "properties": {"name": {"type": "string"}, "family": {"type": "string"},
                            "configuration": {"type": "string"},
                            "kind": {"type": "string", "enum": KINDS}, "role": {"type": "string", "enum": ROLES},
                            "horizontal_grid_spacing": {"type": "string"},
                            "deep_convection": {"type": "string", "enum": DEEP}, "scheme": {"type": "string"},
                            "convection_passage": pid, "grid_passage": pid, "author_label": {"type": "string"}},
             "required": ["name", "family", "configuration", "kind", "role", "horizontal_grid_spacing",
                          "deep_convection", "scheme", "convection_passage", "grid_passage", "author_label"]}
    defn = {"type": "object", "additionalProperties": False,
            "properties": {"term": {"type": "string"}, "meaning": {"type": "string"},
                           "grid_spacing": {"type": "string"}, "passage": pid},
            "required": ["term", "meaning", "grid_spacing", "passage"]}
    return {"type": "object", "additionalProperties": False,
            "properties": {"models": {"type": "array", "items": entry}, "definitions": {"type": "array", "items": defn}},
            "required": ["models", "definitions"]}


def job(lib, it, chars: int = CHARS) -> dict:
    from .papereval import chunks_for
    ps = chunks_for(lib, it, chars)
    body = "\n\n".join(f"[{p['id']}] ({p['section']})\n{p['text']}" for p in ps)
    return {"id": f"modelreg-{it.key}", "kind": "modelreg", "step": "registry", "key": it.key,
            "schema": schema([p["id"] for p in ps]), "chars_sent": sum(len(p["text"]) for p in ps),
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": f"Paper: {it.title} ({it.year})\n\nPassages:\n{body}"
                                                     "\n\nReply with JSON only."}]}


def new_run(lib, *, open_only: bool = True, label: str = "") -> Path:
    from . import localmodel
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    rd = lib.root / "derived" / "model-registry" / (stamp + (f"-{label}" if label else ""))
    rd.mkdir(parents=True, exist_ok=True)
    js = []
    for it in sorted(lib.items(), key=lambda i: i.key):
        if not (it.has_text and it.allowed(S.LOCAL_MODEL)) or (open_only and not it.allowed(S.SEND_OUT)):
            continue
        localmodel.gate(it)
        js.append(job(lib, it))
    (rd / "jobs.jsonl").write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in js))
    (rd / "run.json").write_text(json.dumps({"pass": "model-registry", "prompt_version": PROMPT_VERSION,
                                             "open_only": open_only, "papers": len(js),
                                             "created": dt.datetime.now().isoformat(timespec="seconds")}, indent=1))
    return rd


# ---- aggregation: no thresholds of ours (Mark, 25 Nov 2026: rules are drawn from the literature)

NEIGHBOUR = 2.0     # stated cases count as neighbours of a query within this factor of its grid spacing


def norm_family(f: str) -> str:
    f = re.sub(r"[^a-z0-9]+", " ", (f or "").lower()).strip()
    return re.sub(r"\s*(v(ersion)?\s*)?\d+(\s\d+)*$", "", f).strip()     # CAM6 -> cam, WRF v4 1 -> wrf


def _tokens(t: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z0-9]+", (t or "").lower()))


def _dx(text: str) -> float | None:
    from .claims import grid_spacing_m
    return grid_spacing_m(text)


def build(rd: Path) -> dict:
    """registry.json: every stated case (a configuration whose paper states its deep-convection treatment,
    with the passage), and every definition of a model class the papers give."""
    res = [json.loads(l) for l in (rd / "results.jsonl").read_text().splitlines() if l.strip()]
    cases, defs, papers = [], [], 0
    for r in res:
        if not r.get("ok"):
            continue
        papers += 1
        key = r["id"].removeprefix("modelreg-")
        out = r["output"] or {}
        for m in out.get("models", []):
            fam = norm_family(m.get("family") or m.get("name"))
            cases.append({"key": key, "family": fam, "name": m.get("name", ""),
                          "configuration": m.get("configuration", ""), "role": m.get("role", ""),
                          "grid": m.get("horizontal_grid_spacing", ""), "dx_m": _dx(m.get("horizontal_grid_spacing")),
                          "deep_convection": m.get("deep_convection") if m.get("convection_passage") else "not_stated",
                          "passage": m.get("convection_passage", ""), "author_label": m.get("author_label", "")})
        for d in out.get("definitions", []):
            defs.append({"key": key, **d, "dx_m": _dx(d.get("grid_spacing"))})
    reg = {"run": rd.name, "prompt_version": json.loads((rd / "run.json").read_text()).get("prompt_version"),
           "papers": papers, "cases": cases, "definitions": defs}
    (rd / "registry.json").write_text(json.dumps(reg, ensure_ascii=False, indent=1))
    return reg


def lookup(reg: dict, model_text: str, grid: str, *, exclude: str = "") -> dict | None:
    """What the literature states for configurations like this one: cases of the same family whose grid
    spacing is within a factor NEIGHBOUR (any, if either is unknown), preferring those that share a
    configuration word with the query. {treatment: parametrized|explicit, keys, n} when the stated cases
    agree; None when there are none or they disagree. exclude: a paper to leave out (scoring)."""
    t = " " + re.sub(r"[^a-z0-9]+", " ", (model_text or "").lower()) + " "
    dx = _dx(grid)
    fam = [c for c in reg["cases"] if c["deep_convection"] in ("parametrized", "explicit") and c["key"] != exclude
           and len(c["family"]) >= 2 and re.search(rf"\b{re.escape(c['family'])}(\s?\d|\b)", t)]
    if dx is not None:
        fam = [c for c in fam if c["dx_m"] is None or max(c["dx_m"], dx) / max(1.0, min(c["dx_m"], dx)) <= NEIGHBOUR]
    if not fam:
        return None
    q = _tokens(model_text) - _tokens(" ".join(c["family"] for c in fam))
    close = [c for c in fam if q & (_tokens(c["name"] + " " + c["configuration"]) - _tokens(c["family"]))]
    for group in (close, fam):
        ts = {c["deep_convection"] for c in group}
        if len(ts) == 1:
            return {"treatment": ts.pop(), "keys": sorted({c["key"] for c in group}), "n": len(group)}
        if group:
            return None          # the literature disagrees: no rule
    return None


def score_stated(reg: dict) -> dict:
    """Leave one paper out: for each stated case of a configuration the paper itself ran, hide it and ask
    the rest of the registry. Coverage (how often it answers) and accuracy (how often the answer matches
    what the paper states)."""
    tot = answered = right = 0
    for c in reg["cases"]:
        if c["deep_convection"] not in ("parametrized", "explicit") or c["role"] != "run_in_this_paper":
            continue
        tot += 1
        r = lookup(reg, f"{c['name']} {c['configuration']} {c['family']}", c["grid"], exclude=c["key"])
        if r:
            answered += 1
            right += r["treatment"] == c["deep_convection"]
    return {"stated_cases": tot, "answered": answered, "coverage": round(answered / max(1, tot), 3),
            "correct": right, "accuracy": round(right / max(1, answered), 3)}


def explicit_class(reg: dict | None, label: str, dx_m: float | None) -> str:
    """les, cpm or explicit_model for a run with explicit deep convection: the authors' own label first,
    then the definitions the literature gives (only where every definition that bears on the grid
    spacing agrees); explicit_model when neither settles it."""
    lab = (label or "").lower()
    if re.search(r"large[- ]eddy|\bles\b", lab):
        return "les"
    if re.search(r"convection[- ](permitting|resolving)|cloud[- ](system[- ])?resolving|storm[- ]resolving|\bcrm\b|\bsrm\b",
                 lab):
        return "cpm"
    if reg is None or dx_m is None:
        return "explicit_model"
    les = [d["dx_m"] for d in reg["definitions"] if d["dx_m"] and re.search(r"large[- ]eddy|\bles\b", d["term"].lower())]
    cpm = [d["dx_m"] for d in reg["definitions"] if d["dx_m"] and re.search(
        r"convection[- ](permitting|resolving)|cloud[- ](system[- ])?resolving|storm[- ]resolving", d["term"].lower())]
    if les and dx_m <= min(les) and (not cpm or dx_m < min(cpm)):
        return "les"
    if cpm and les and dx_m > max(les) and dx_m <= max(cpm):
        return "cpm"
    return "explicit_model"
