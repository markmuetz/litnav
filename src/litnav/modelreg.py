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

PROMPT_VERSION = 1
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
                            "convection_passage": pid, "grid_passage": pid},
             "required": ["name", "family", "configuration", "kind", "role", "horizontal_grid_spacing",
                          "deep_convection", "scheme", "convection_passage", "grid_passage"]}
    return {"type": "object", "additionalProperties": False,
            "properties": {"models": {"type": "array", "items": entry}}, "required": ["models"]}


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


# ---- aggregation

def dx_class(text: str) -> str:
    """Grid-spacing class: les (<=500 m), km (<=4 km), grey (4-10 km), coarse (>10 km), unknown.
    Degrees and spectral truncations count as coarse (they are always >10 km in practice here)."""
    from .claims import grid_spacings_m
    xs = grid_spacings_m(text)
    if not xs:
        t = (text or "").lower()
        return "coarse" if re.search(r"\d\s*(°|deg|degree)|\bt\d{2,4}\b|\btl?\d{2,4}\b|\bn\d{2,4}\b", t) else "unknown"
    dx = min(xs)
    return "les" if dx <= 500 else "km" if dx <= 4000 else "grey" if dx <= 10_000 else "coarse"


def norm_family(f: str) -> str:
    f = re.sub(r"[^a-z0-9]+", " ", (f or "").lower()).strip()
    return re.sub(r"\s*(v(ersion)?\s*)?\d+(\s\d+)*$", "", f).strip()     # CAM6 -> cam, WRF v4 1 -> wrf


def build(rd: Path) -> dict:
    """registry.json: per (family, grid-spacing class), the papers stating parametrized or explicit deep
    convection (with passages), and a verdict where they agree."""
    res = [json.loads(l) for l in (rd / "results.jsonl").read_text().splitlines() if l.strip()]
    by = defaultdict(lambda: {"parametrized": {}, "explicit": {}, "both": {}, "not_stated": 0})
    papers = 0
    for r in res:
        if not r.get("ok"):
            continue
        papers += 1
        key = r["id"].removeprefix("modelreg-")
        for m in (r["output"] or {}).get("models", []):
            fam = norm_family(m.get("family") or m.get("name"))
            if not fam:
                continue
            e = by[(fam, dx_class(m.get("horizontal_grid_spacing", "")))]
            d = m.get("deep_convection")
            if d in ("parametrized", "explicit", "both") and m.get("convection_passage"):
                e[d].setdefault(key, m["convection_passage"])
            else:
                e["not_stated"] += 1
    out = []
    for (fam, cls), e in sorted(by.items()):
        n_p, n_e = len(e["parametrized"]), len(e["explicit"])
        verdict = ("parametrized" if n_p and not n_e else "explicit" if n_e and not n_p else
                   "disputed" if n_p and n_e else "")
        out.append({"family": fam, "dx_class": cls, "verdict": verdict, "papers_parametrized": n_p,
                    "papers_explicit": n_e, "papers_both": len(e["both"]), "not_stated": e["not_stated"],
                    "sources": {k: v for k, v in {**e["parametrized"], **e["explicit"]}.items()}})
    reg = {"run": rd.name, "papers": papers, "entries": out}
    (rd / "registry.json").write_text(json.dumps(reg, ensure_ascii=False, indent=1))
    return reg


def lookup(reg: dict, model_text: str, grid: str) -> tuple[str, list[str]] | None:
    """(class, source keys) for a run whose paper does not state its convection treatment, from the
    registry entry of its family at its grid-spacing class (an unknown class takes the family's entries
    if they all agree). None when the registry has no agreed answer."""
    t = " " + re.sub(r"[^a-z0-9]+", " ", (model_text or "").lower()) + " "
    cls = dx_class(grid)
    hits = [e for e in reg["entries"] if e["verdict"] in ("parametrized", "explicit")
            and len(e["family"]) >= 2 and re.search(rf"\b{re.escape(e['family'])}(\s?\d|\b)", t)]
    if cls != "unknown":
        hits = [e for e in hits if e["dx_class"] == cls]
    verdicts = {e["verdict"] for e in hits}
    if len(verdicts) != 1:
        return None
    v = verdicts.pop()
    keys = sorted({k for e in hits for k in e["sources"]})
    return ("parametrized_model" if v == "parametrized" else "les" if cls == "les" else "cpm"), keys
