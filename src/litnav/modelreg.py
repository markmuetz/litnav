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

# How a run is matched to other papers' stated cases. "bracket" (the default) has no parameter: the nearest
# stated case finer than the run and the nearest coarser, of the same model; the papers fix the window.
# ("factor", f) and "exact" are kept to measure how sensitive the answers are to that choice.
MATCH = "bracket"


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


def _same_model(reg: dict, model_text: str, exclude: str) -> list[dict]:
    """Stated cases of the run's model: the same family (whole word), narrowed to those sharing a
    configuration word with the run when any do."""
    t = " " + re.sub(r"[^a-z0-9]+", " ", (model_text or "").lower()) + " "
    fam = [c for c in reg["cases"] if c["deep_convection"] in ("parametrized", "explicit") and c["key"] != exclude
           and len(c["family"]) >= 2 and re.search(rf"\b{re.escape(c['family'])}(\s?\d|\b)", t)]
    q = _tokens(model_text) - _tokens(" ".join(c["family"] for c in fam))
    close = [c for c in fam if q & (_tokens(c["name"] + " " + c["configuration"]) - _tokens(c["family"]))]
    return close or fam


def _matched(cases: list[dict], dx: float | None, match) -> list[dict]:
    if dx is None:
        return cases
    known = [c for c in cases if c["dx_m"] is not None]
    if match == "exact":
        return [c for c in known if abs(c["dx_m"] - dx) <= 1e-6 * dx]
    if isinstance(match, tuple):
        f = match[1]
        return [c for c in known if max(c["dx_m"], dx) / max(1.0, min(c["dx_m"], dx)) <= f]
    finer = [c["dx_m"] for c in known if c["dx_m"] <= dx]
    coarser = [c["dx_m"] for c in known if c["dx_m"] >= dx]
    if not finer or not coarser:
        return []                     # outside what the papers state: no extrapolation
    lo, hi = max(finer), min(coarser)
    return [c for c in known if c["dx_m"] in (lo, hi)]


def lookup(reg: dict, model_text: str, grid: str, *, exclude: str = "", match=None) -> dict | None:
    """What the literature states for configurations like this one. Returns None when no paper states
    anything for it, else {treatment, keys, n, split}: treatment is parametrized or explicit when the
    matched papers agree, None when they disagree (the split says how). exclude: a paper to leave out."""
    cases = _matched(_same_model(reg, model_text, exclude), _dx(grid), match or MATCH)
    if not cases:
        return None
    split = {t: len({c["key"] for c in cases if c["deep_convection"] == t}) for t in ("parametrized", "explicit")}
    ts = {c["deep_convection"] for c in cases}
    return {"treatment": ts.pop() if len(ts) == 1 else None, "keys": sorted({c["key"] for c in cases}),
            "n": len(cases), "split": split}


def score_stated(reg: dict, match=None) -> dict:
    """Leave one paper out: for each stated case of a configuration the paper itself ran, hide it and ask
    the rest of the registry. coverage: how often it answers; accuracy: how often the answer matches what
    the paper states; disputed: how often the other papers disagree."""
    tot = answered = right = disputed = 0
    for c in reg["cases"]:
        if c["deep_convection"] not in ("parametrized", "explicit") or c["role"] != "run_in_this_paper":
            continue
        tot += 1
        r = lookup(reg, f"{c['name']} {c['configuration']} {c['family']}", c["grid"], exclude=c["key"], match=match)
        if r and r["treatment"]:
            answered += 1
            right += r["treatment"] == c["deep_convection"]
        elif r:
            disputed += 1
    return {"match": str(match or MATCH), "stated_cases": tot, "answered": answered,
            "coverage": round(answered / max(1, tot), 3), "correct": right,
            "accuracy": round(right / max(1, answered), 3), "disputed": disputed}


# ---- the class vocabulary, induced from the papers' own definitions

CLASSES_SYSTEM = """You are given definitions of classes of numerical model, each taken from a scientific paper
(the term as the paper writes it, what the paper says it means, any grid spacing the paper ties to it). Group
the terms that name the same class of model, using only what the definitions say, not what you know. For each
class give: id (a short snake_case name from the most common term), name, terms (every term in the group, as
written), deep_convection (what the definitions say: explicit, parametrized, either, or not_said), and refs
(the numbers of the definitions in the group). Terms that are not classes of model (a range of grid spacing,
such as the grey zone, is fine: treat it as a class) go in no group. Reply with JSON only."""

CLASSES_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["classes"], "properties": {
    "classes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["id", "name", "terms", "deep_convection", "refs"],
                "properties": {"id": {"type": "string"}, "name": {"type": "string"},
                               "terms": {"type": "array", "items": {"type": "string"}},
                               "deep_convection": {"type": "string",
                                                   "enum": ["explicit", "parametrized", "either", "not_said"]},
                               "refs": {"type": "array", "items": {"type": "integer"}}}}}}}


def induce_classes(rd: Path, *, model: str = "claude-opus-5-5") -> dict:
    """classes.json: the papers' terms for classes of model, grouped by a strong model from the
    definitions alone (a proposal that cites them), with the grid spacings each class's definitions state
    and the papers that state them."""
    from .g0 import _claude
    reg = json.loads((rd / "registry.json").read_text())
    defs = reg["definitions"]
    lines = "\n".join(f"{i}. term: {d['term']} | meaning: {d['meaning']} | grid spacing: {d.get('grid_spacing') or '-'}"
                      for i, d in enumerate(defs, 1))
    out = _claude(model, CLASSES_SYSTEM, f"Definitions ({len(defs)}):\n{lines}", CLASSES_SCHEMA)
    classes = []
    for c in out.get("classes", []):
        refs = [defs[i - 1] for i in c.get("refs", []) if isinstance(i, int) and 0 < i <= len(defs)]
        dxs = sorted(d["dx_m"] for d in refs if d.get("dx_m"))
        classes.append({**c, "papers": sorted({d["key"] for d in refs}),
                        "grid_spacings_m": dxs, "span_m": [dxs[0], dxs[-1]] if dxs else None,
                        "sources": [{"key": d["key"], "passage": d.get("passage"), "grid_spacing": d.get("grid_spacing")}
                                    for d in refs]})
    voc = {"run": rd.name, "model": model, "definitions": len(defs), "classes": classes}
    (rd / "classes.json").write_text(json.dumps(voc, ensure_ascii=False, indent=1))
    return voc


def load(rd: Path) -> dict:
    """A registry with its induced classes (if any), for evidence_type(reg=...)."""
    reg = json.loads((Path(rd) / "registry.json").read_text())
    cp = Path(rd) / "classes.json"
    reg["classes"] = json.loads(cp.read_text())["classes"] if cp.exists() else []
    return reg


def _norm_term(t: str) -> str:
    t = re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()
    return re.sub(r"s$", "", t)


def explicit_class(reg: dict | None, label: str, dx_m: float | None) -> str:
    """The class of a run with explicit deep convection, from the induced vocabulary only: a class one of
    whose terms appears in the authors' label; else the one explicit class whose definitions' grid
    spacings span the run's; else explicit_model. No terms or thresholds of ours."""
    classes = [c for c in (reg or {}).get("classes", []) if c.get("deep_convection") in ("explicit", "either")]
    if not classes:
        return "explicit_model"
    lab = " " + _norm_term(label) + " "
    named = {c["id"] for c in classes for t in c["terms"] if len(_norm_term(t)) > 1 and f" {_norm_term(t)} " in lab}
    if len(named) == 1:
        return named.pop()
    if dx_m is not None:
        inside = {c["id"] for c in classes if c.get("span_m") and c["span_m"][0] <= dx_m <= c["span_m"][1]}
        if len(inside) == 1:
            return inside.pop()
    return "explicit_model"
