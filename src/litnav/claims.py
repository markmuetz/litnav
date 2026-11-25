"""Claim mentions: what a paper claims, as records for a claim graph.

A mention is one claim as one paper makes it: a standalone statement in the
model's own words, located by a chunk id, with its claim type (shown here,
restated from other work, assumed, speculation), evidence type, experiment,
conditions, hedging and, for a number, its value. The quoted passage that
grounds it is kept apart from the record (`passages`), for checking only.

The local model reads the paper section by section (`groups`): consecutive
sections fill one call up to a character budget, so a small model with a
short context reads the whole paper, and a large one reads it in a few calls.
Every call also sees the title and abstract, for context.

This prompt is the production one (`litnav claims extract`) and the one
`litnav paper-eval --task mentions` measures, so the eval measures what runs.
Model evidence is classified by code, not by the model (EVIDENCE_RULE): the
model records whether deep convection is parametrized, the grid spacing and
the paper's own name for the run, so the thresholds can change without
re-extraction. The hedging levels are v0: not yet settled.
"""
from __future__ import annotations

import json
import re
import secrets

PROMPT_VERSION = 4              # v4: a runs table per paper (configuration and schemes), mentions name their run
SCHEMA_VERSION = "0.4"
EVIDENCE_RULE = 2               # evidence_type() below; rule 1 Mark 23 Nov 2026, rule 2 Mark 25 Nov 2026
LES_MAX_M = 500                 # grid spacing at or below this, with no deep-convection scheme: les
PARAM_MIN_M = 10_000            # rule 2: coarser than this, scheme not stated -> parametrized (inferred)
MAX_ITEMS = 40                  # mentions a call; report counts calls that hit it
GROUP_CHARS = 20_000            # passages per call: fits a 32k-token context with room to think
SKIP_KINDS = {"ack", "front"}   # not claims; the title and abstract go to every call anyway

CLAIM_TYPES = ["shown", "restated", "assumed", "speculation"]
EVIDENCE_KINDS = ["observations", "reanalysis", "model", "theory", "mixed", "unknown"]     # what the model labels
EVIDENCE_TYPES = ["observations", "reanalysis", "les", "cpm", "parametrized_model", "idealized_model", "theory",
                  "mixed", "unknown"]                                                    # the record's, by rule
YNU = ["yes", "no", "unknown"]
HEDGING = ["strong", "moderate", "weak"]
SOURCES = ["text", "figure", "table"]
# the runs table (v4; Mark, 24 Nov 2026): a run's configuration, scheme by scheme
COMPONENT_KINDS = ["deep_convection", "shallow_convection", "microphysics", "boundary_layer", "turbulence",
                   "radiation", "land_surface", "gravity_wave_drag"]
COMPONENT_STATES = ["used", "off", "not_stated"]
MODEL_FAMILIES = ["physical", "idealized", "machine_learning", "hybrid", "unknown"]
DOMAINS = ["global", "limited_area", "idealized", "single_column", "unknown"]
EXPERIMENT_KINDS = ["model_run", "observational_analysis"]
DATASET_KINDS = ["satellite", "radar", "in_situ", "station", "field_campaign", "reanalysis", "model_output",
                 "other"]
COUPLINGS = ["atmosphere_only", "coupled", "unknown"]
MAX_RUNS = 20                   # entries in the experiments table
RUNS_CHARS = 40_000             # methods passages for the experiments call
_METHODS = re.compile(r"method|model|experiment|simulat|set-?up|configur|design|numerical|data|"
                      r"\brun|framework|approach", re.I)
RUNS_SLOT = "{{RUNS}}"          # where a section call's prompt takes the paper's experiments table

SYSTEM = (
    "You extract the claims an academic paper makes, for a claim graph that links claims across papers. "
    "You see the paper's title and abstract for context, then numbered passages from one part of the "
    "paper. Record each substantive claim these passages make: findings, conclusions, and the key facts "
    "of the method and data. Include claims the paper takes from other work, labelled restated. Skip "
    "trivia, descriptions of figure layout, and plans for future work. Only claims stated in the "
    "passages, not in the abstract (unless the abstract is among the passages).\n"
    "For each claim:\n"
    "- statement: one standalone sentence a reader understands without the paper: name the model, "
    "region, period and quantity; no 'it', 'we' or 'this study'. In your own words: never copy a run of "
    "words from the paper. Keep every number with its units, and the direction of each effect.\n"
    "- chunk: the id of the passage that states it. quote: the shortest exact excerpt of that passage "
    "that supports it (at most 40 words, copied exactly; it is used only to check the record).\n"
    "- source: figure or table if the claim reports what a figure or table shows (a caption, or text "
    "describing it), otherwise text.\n"
    "- claim_type: shown (this paper's own results demonstrate it); restated (the paper attributes it "
    "to other work, usually with a citation, or states it as known background); assumed (a premise or "
    "method choice the paper takes as given); speculation (offered as a possibility or explanation "
    "without support in this paper). A claim the paper attributes to cited work is restated; the "
    "paper's own result compared with cited work is shown.\n"
    "- evidence: what the claim rests on (for a restated claim, the cited work's): observations, "
    "reanalysis, model (any numerical simulation), theory, mixed, or unknown if the passages don't say: "
    "don't guess.\n"
    "- experiment_name: if the claim rests on a model run or observational analysis of this paper, its name "
    "from the experiments table given above the passages, exactly as written there; '' otherwise (for "
    "restated claims, and for experiments not in the table).\n"
    "- experiment, only for a model run not in the experiments table ('' or unknown otherwise, or if not stated): the model; "
    "resolution: the horizontal grid spacing as written (e.g. '2.2 km', '250 m', '0.25 degrees'); "
    "method; deep_convection_parametrized: yes if the run uses a deep-convection (cumulus) scheme, no "
    "if convection is explicit, unknown if not stated; run_name: the paper's own name for the kind of "
    "run (e.g. 'LES', 'CRM', 'convection-permitting', 'storm-resolving', 'km-scale', 'GCM'). CRM, CPM, "
    "convection-allowing, storm-resolving (SRM, GSRM, GCRM) and LES are names for runs without a "
    "deep-convection scheme; 'km-scale' alone does not say whether convection is parametrized.\n"
    "- conditions: the region, regime, period and resolution it holds for ('' where not stated).\n"
    "- hedging (provisional levels): strong (shows, demonstrates, is), moderate (suggests, indicates, "
    "likely), weak (may, could, possibly). hedge_words: the paper's own hedge, a few words ('' if none).\n"
    "- value: for a claim about one number, its value, units and uncertainty range low and high, as "
    "written; '' in each field if the claim has no single number or no range.\n"
    "Reply with JSON only.")


def schema(ids: list[str], max_items: int = MAX_ITEMS) -> dict:
    s = {"type": "string"}
    return {"type": "object", "properties": {"mentions": {"type": "array", "maxItems": max_items, "items": {
        "type": "object", "properties": {
            "statement": s,
            "chunk": {"type": "string", "enum": ids},
            "quote": s,
            "source": {"type": "string", "enum": SOURCES},
            "claim_type": {"type": "string", "enum": CLAIM_TYPES},
            "evidence": {"type": "string", "enum": EVIDENCE_KINDS},
            "experiment_name": s,
            "experiment": {"type": "object", "properties": {
                "model": s, "resolution": s, "method": s,
                "deep_convection_parametrized": {"type": "string", "enum": YNU}, "run_name": s},
                "required": ["model", "resolution", "method", "deep_convection_parametrized", "run_name"],
                "additionalProperties": False},
            "conditions": {"type": "object", "properties": {"region": s, "regime": s, "period": s,
                                                            "resolution": s},
                           "required": ["region", "regime", "period", "resolution"],
                           "additionalProperties": False},
            "hedging": {"type": "string", "enum": HEDGING},
            "hedge_words": s,
            "value": {"type": "object", "properties": {"value": s, "units": s, "low": s, "high": s},
                      "required": ["value", "units", "low", "high"], "additionalProperties": False}},
        "required": ["statement", "chunk", "quote", "source", "claim_type", "evidence", "experiment_name", "experiment",
                     "conditions", "hedging", "hedge_words", "value"],
        "additionalProperties": False}}}, "required": ["mentions"], "additionalProperties": False}


RUNS_SYSTEM = (
    "You list the experiments an academic paper reports, for a claim graph that compares results across "
    "models and observations. You see the paper's title and abstract, then numbered passages from its "
    "methods, data and model description. List each distinct experiment the paper itself makes (not those "
    "of other work it only cites): each model run or set of runs, and each observational analysis (an "
    "analysis of observations, reanalysis or archived model output). For each:\n"
    "- kind: model_run or observational_analysis. name: the paper's own name for it, as written (e.g. "
    "'295dT1p25', 'CTRL', 'N2560 GAL9'); if it has none, a short description ('the 2.2 km "
    "convection-permitting run', 'the CloudSat compositing analysis').\n"
    "- region, period: as written ('' if not stated).\n"
    "- method: for an observational analysis, how the data are analysed, as written (e.g. 'tracking with "
    "PyFLEXTRKR', 'compositing on rain rate', 'a 10 mm/h threshold'); method_cited: the reference given for "
    "it ('' if none). '' for a model run unless it is analysed in a named way.\n"
    "- datasets: for an observational analysis, each dataset it uses: name and version as written (e.g. "
    "'CloudSat 2B-GEOPROF', 'R05'; 'ERA5', ''); kind (" + ", ".join(DATASET_KINDS) + "); resolution as "
    "written ('' if not stated); chunk: the id of the passage that names it. A model run's forcing or "
    "evaluation data can be listed too. An empty list if none.\n"
    "- For a model run (all '' or unknown, and components an empty list, for an observational analysis): "
    "model, version, configuration: as written ('' if not stated), e.g. 'Met Office Unified Model', "
    "'vn11.1', 'RA2T'. configuration_cited: the reference the paper gives for the configuration (author and "
    "year as written), '' if none. family: physical (a full numerical weather or climate model, LES or CRM), "
    "idealized (a simplified dynamical model: quasi-geostrophic, shallow-water, a dry or two-layer model, a "
    "conceptual or toy model), machine_learning (a data-driven model such as GraphCast), hybrid, or unknown. horizontal_grid_spacing, vertical_levels: "
    "as written ('' if not stated). domain: global, limited_area, idealized, single_column or unknown. "
    "coupling: atmosphere_only, coupled or unknown; ocean_model as written ('' if none).\n"
    "- components, for a model run: one entry for each kind: " + ", ".join(COMPONENT_KINDS) + ". For each: "
    "state used (the run has such a scheme), off (the paper says it is switched off or resolved "
    "explicitly), or not_stated (the paper doesn't say: don't guess from what such models usually have); "
    "scheme: its name as written ('' if not stated); moments: for microphysics, the number of moments if "
    "stated ('' otherwise); chunk: the id of the passage that says so ('' if not stated).\n"
    "A run whose deep convection is not described is not_stated, even if the paper calls it "
    "convection-permitting, storm-resolving or km-scale: say it in name, not in the components. A paper "
    "with neither (pure theory, a review) has an empty list. Reply with JSON only.")


def runs_schema(ids: list[str]) -> dict:
    s = {"type": "string"}
    chunk = {"type": "string", "enum": ids + [""]}
    comp = {"type": "object", "additionalProperties": False,
            "required": ["kind", "state", "scheme", "moments", "chunk"],
            "properties": {"kind": {"type": "string", "enum": COMPONENT_KINDS},
                           "state": {"type": "string", "enum": COMPONENT_STATES},
                           "scheme": s, "moments": s, "chunk": chunk}}
    ds = {"type": "object", "additionalProperties": False, "required": ["name", "version", "kind", "resolution",
                                                                          "chunk"],
          "properties": {"name": s, "version": s, "kind": {"type": "string", "enum": DATASET_KINDS},
                         "resolution": s, "chunk": chunk}}
    fields = {"kind": {"type": "string", "enum": EXPERIMENT_KINDS}, "name": s, "region": s, "period": s,
              "method": s, "method_cited": s, "datasets": {"type": "array", "items": ds},
              "model": s, "version": s, "configuration": s, "configuration_cited": s,
              "family": {"type": "string", "enum": MODEL_FAMILIES},
              "horizontal_grid_spacing": s, "vertical_levels": s,
              "domain": {"type": "string", "enum": DOMAINS}, "coupling": {"type": "string", "enum": COUPLINGS},
              "ocean_model": s, "components": {"type": "array", "items": comp}}
    ex = {"type": "object", "additionalProperties": False, "properties": fields, "required": list(fields)}
    return {"type": "object", "additionalProperties": False, "required": ["experiments"],
            "properties": {"experiments": {"type": "array", "maxItems": MAX_RUNS, "items": ex}}}


def table(out: dict | None) -> list[dict] | None:
    """A reply's experiments table (v4; 'runs' in the first draft), or None if it has none."""
    if not isinstance(out, dict):
        return None
    t = out.get("experiments", out.get("runs"))
    return t if isinstance(t, list) else None


def entry_name(e: dict) -> str:
    return (e.get("name") or e.get("run_name") or "").strip()


def mention_experiment(m: dict) -> str:
    return m.get("experiment_name") or m.get("run") or ""


def component(run: dict, kind: str) -> dict:
    """A run's component of one kind (not_stated if the reply left it out)."""
    for c in run.get("components") or []:
        if c.get("kind") == kind:
            return c
    return {"kind": kind, "state": "not_stated", "scheme": "", "moments": "", "chunk": ""}


def find_run(runs: list[dict] | None, name: str) -> dict | None:
    k = (name or "").strip().lower()
    if not k:
        return None
    for r in runs or []:
        if entry_name(r).lower() == k:
            return r
    return None


# ---- reading the paper in groups of sections

def chunks(lib, it) -> list[dict]:
    from . import index
    con = index.connect(lib, readonly=True)
    try:
        return [dict(r) for r in con.execute(
            "SELECT id, section, section_title, kind, start, text FROM chunks WHERE key=? ORDER BY ord",
            (it.key,))]
    finally:
        con.close()


def context(it, rows: list[dict], limit: int = 2500) -> str:
    ab = " ".join(r["text"] for r in rows if r["kind"] == "abstract")
    if ab:
        return f"Paper: {it.title} ({it.year})\nAbstract: {ab[:limit]}"
    first = next((r["text"] for r in rows if r["kind"] not in SKIP_KINDS | {"caption"}), "")
    return f"Paper: {it.title} ({it.year})\nOpening (no abstract found): {first[:limit]}"


def groups(rows: list[dict], budget: int = GROUP_CHARS) -> list[list[dict]]:
    """Consecutive sections, whole where they fit, up to `budget` characters a
    group; a section longer than the budget is split between its passages."""
    secs: list[list[dict]] = []
    for r in rows:
        if r["kind"] in SKIP_KINDS:
            continue
        name = r.get("section") or r.get("section_title") or ""
        if secs and (secs[-1][0].get("section") or secs[-1][0].get("section_title") or "") == name:
            secs[-1].append(r)
        else:
            secs.append([r])
    out: list[list[dict]] = []
    size = 0
    for sec in secs:
        n = sum(len(r["text"]) for r in sec)
        if out and size + n <= budget:
            out[-1].extend(sec)
            size += n
            continue
        cur, size = [], 0
        for r in sec:
            if cur and size + len(r["text"]) > budget:
                out.append(cur)
                cur, size = [], 0
            cur.append(r)
            size += len(r["text"])
        out.append(cur)
    return [g for g in out if g]


def _body(group: list[dict]) -> str:
    return "\n\n".join(f"[{r['id']}] ({r.get('section_title') or r['kind']}{', caption' if r['kind'] == 'caption' else ''})"
                       f"\n{r['text']}" for r in group)


def messages(it, ctx: str, group: list[dict]) -> list[dict]:
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{ctx}\n\nRuns in this paper (from its methods):\n{RUNS_SLOT}\n\n"
                                        f"Passages:\n{_body(group)}\n\nReply with JSON only."}]


def methods_rows(rows: list[dict], budget: int = RUNS_CHARS) -> list[dict]:
    """The passages a runs call reads: sections whose titles look like methods,
    model description or experiments, in order, up to `budget` characters; the
    body from the start if no title matches."""
    body = [r for r in rows if r["kind"] not in SKIP_KINDS | {"abstract", "caption", "references"}]
    pick = [r for r in body if _METHODS.search(r.get("section_title") or "")] or body
    out, n = [], 0
    for r in pick:
        if out and n + len(r["text"]) > budget:
            break
        out.append(r)
        n += len(r["text"])
    return out


def runs_job(it, rows: list[dict], ctx: str, *, prefix: str = "mentions") -> dict:
    g = methods_rows(rows)
    ids = [r["id"] for r in g]
    return {"id": f"{prefix}-{it.key}--runs", "kind": "eval", "task": "mentions", "key": it.key, "role": "runs",
            "schema": runs_schema(ids), "passages": ids, "chars_sent": sum(len(r["text"]) for r in g),
            "sections": sorted({r.get("section_title") or r["kind"] for r in g}),
            "messages": [{"role": "system", "content": RUNS_SYSTEM},
                         {"role": "user", "content": f"{ctx}\n\nPassages:\n{_body(g)}\n\nReply with JSON only."}]}


def jobs(lib, it, *, budget: int = GROUP_CHARS, prefix: str = "mentions") -> list[dict]:
    """The runs call, then one model call per group of sections; each section
    call waits for the runs call ("after") and takes its table (copyfix.fill)."""
    rows = chunks(lib, it)
    ctx = context(it, rows)
    rj = runs_job(it, rows, ctx, prefix=prefix)
    out = [rj]
    for n, g in enumerate(groups(rows, budget), 1):
        ids = [r["id"] for r in g]
        out.append({"id": f"{prefix}-{it.key}--g{n:02d}", "kind": "eval", "task": "mentions", "key": it.key,
                    "group": n, "schema": schema(ids), "messages": messages(it, ctx, g), "passages": ids,
                    "sections": sorted({r.get("section_title") or r["kind"] for r in g}),
                    "chars_sent": sum(len(r["text"]) for r in g),
                    # the runner rewrites copying statements and checks them in the same run (copyfix)
                    "followup": "mentions", "title": it.title or "", "after": rj["id"]})
    return out


# ---- replies to records

def _num(x: str):
    try:
        return float(str(x).replace(",", "").replace("−", "-").strip())
    except ValueError:
        return None


def _value(v: dict | None) -> dict | None:
    v = v or {}
    if not str(v.get("value", "")).strip():
        return None
    return {"value": _num(v["value"]), "as_written": str(v["value"]).strip(), "units": v.get("units", ""),
            "low": _num(v.get("low", "")), "high": _num(v.get("high", ""))}


def new_id(prefix: str = "m") -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def records(key: str, replies: list[dict], chunk_info: dict, *, run: str = "",
            runs: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Mention records and their passages (kept apart) from the replies of one
    paper's calls. chunk_info: chunk id -> {section_title, page}. runs: the
    paper's runs table (v4), whose facts a mention naming a run takes."""
    recs, quotes = [], []
    for out in replies:
        for m in (out or {}).get("mentions") or []:
            mid = new_id()
            ci = chunk_info.get(m.get("chunk"), {})
            recs.append({
                "id": mid, "type": "mention", "schema_version": SCHEMA_VERSION, "paper": key,
                "chunk": m.get("chunk"), "section": ci.get("section_title"), "page": ci.get("page"),
                "statement": m.get("statement", ""), "source": m.get("source"),
                "claim_type": m.get("claim_type"), "evidence_type": evidence_type(m, runs),
                "evidence_rule": EVIDENCE_RULE, "evidence_inferred": evidence_inferred(m, runs),
                "evidence": m.get("evidence"),
                "experiment_name": entry_name(find_run(runs, mention_experiment(m)) or {}),
                "experiment": {**(m.get("experiment") or {}),
                               "grid_spacing_m": grid_spacing_m((m.get("experiment") or {}).get("resolution"))},
                "conditions": m.get("conditions") or {},
                "hedging": {"level": m.get("hedging"), "words": m.get("hedge_words", "")},
                "value": _value(m.get("value")), "review": "unchecked", "run": run,
                "prompt_version": PROMPT_VERSION})
            quotes.append({"mention": mid, "chunk": m.get("chunk"), "quote": m.get("quote", "")})
    return recs, quotes


def grounded(quote: str, chunk_text: str) -> bool:
    """The quote is in the passage, after normalising case, whitespace and
    hyphenation at line ends."""
    def norm(s):
        s = re.sub(r"-\s*\n\s*", "", s)
        return re.sub(r"\s+", " ", s).strip().lower()
    q = norm(quote)
    return bool(q) and q in norm(chunk_text)


# ---- evidence type, by rule (EVIDENCE_RULE; the thresholds live here, not in the prompt)

_UNITS = {"m": 1.0, "metres": 1.0, "meters": 1.0, "km": 1000.0, "kilometres": 1000.0, "kilometers": 1000.0,
          "deg": 111_000.0, "degree": 111_000.0, "degrees": 111_000.0, "°": 111_000.0}
_SPACING = re.compile(r"(\d+(?:\.\d+)?|\d*\.\d+)\s*(?:(?:[-–]|to|and)\s*(\d+(?:\.\d+)?)\s*)?"
                      r"(kilometres|kilometers|km|metres|meters|m|degrees|degree|deg|°)(?![a-z])", re.IGNORECASE)


def grid_spacings_m(text: str | None) -> list[float]:
    """Every grid spacing written in `text`, in metres ('1-4 km' gives both ends)."""
    out = []
    for a, b, u in _SPACING.findall(text or ""):
        f = _UNITS[u.lower()]
        out += [float(a) * f] + ([float(b) * f] if b else [])
    return out


def grid_spacing_m(text: str | None) -> float | None:
    """The finest grid spacing written, in metres (None if none)."""
    xs = grid_spacings_m(text)
    return min(xs) if xs else None


def _model_class(param: str | None, dx: float | None, rule: int = EVIDENCE_RULE) -> str:
    if param == "yes":
        return "parametrized_model"
    if param == "no":
        if dx is None:
            return "unknown"
        return "les" if dx <= LES_MAX_M else "cpm"
    if rule >= 2 and dx is not None and dx > PARAM_MIN_M:
        return "parametrized_model"         # rule 2: not stated, coarser than 10 km (Mark, 25 Nov 2026)
    return "unknown"


def run_facts(run: dict) -> dict:
    """What the evidence rule needs from a runs-table entry, as v3 facts."""
    dc = component(run, "deep_convection")["state"]
    return {"model": run.get("model", ""), "resolution": run.get("horizontal_grid_spacing", ""),
            "method": "", "run_name": entry_name(run),
            "deep_convection_parametrized": {"used": "yes", "off": "no"}.get(dc, "unknown"),
            "family": run.get("family", "unknown")}


def evidence_type(m: dict, runs: list[dict] | None = None, rule: int = EVIDENCE_RULE) -> str:
    """With an experiments table (v4), a mention naming a model run takes that
    run's facts. A machine-learning model has no class (unknown); an idealized
    one (quasi-geostrophic, shallow-water, dry...) is idealized_model (rule 2)."""
    if m.get("evidence") == "model" and runs is not None:
        r = find_run(runs, mention_experiment(m))
        if r and r.get("kind", "model_run") == "model_run":
            f = run_facts(r)
            if f["family"] == "machine_learning":
                return "unknown"
            if f["family"] == "idealized":
                return "idealized_model" if rule >= 2 else "unknown"
            return evidence_type({**m, "run": "", "experiment_name": "", "experiment": f}, rule=rule)
    return _evidence_type_v3(m, rule)


def evidence_inferred(m: dict, runs: list[dict] | None = None) -> bool:
    """The class comes from rule 2's inference (scheme not stated, coarser than 10 km)."""
    return evidence_type(m, runs) != evidence_type(m, runs, rule=1) and \
        evidence_type(m, runs) in ("parametrized_model", "mixed")


def _evidence_type_v3(m: dict, rule: int = EVIDENCE_RULE) -> str:
    """The record's evidence type from the model's facts.
    Rule 1 (Mark, 23 Nov 2026): a deep-convection scheme -> parametrized_model,
    whatever the grid spacing; otherwise grid spacing <= 500 m -> les, else cpm
    (no upper bound: grey-zone runs keep their spacing and can be filtered);
    missing facts -> unknown. Several grid spacings that fall in different
    classes -> mixed.
    Rule 2 (Mark, 25 Nov 2026): as rule 1, but a run whose scheme is not stated
    and whose grid spacing is coarser than 10 km is parametrized_model: in
    practice such runs parametrize deep convection unless the paper says it is
    explicit (which is 'no' here). Records mark it as inferred."""
    ev = m.get("evidence")
    if ev != "model":
        return ev if ev in EVIDENCE_TYPES else "unknown"
    e = m.get("experiment") or {}
    param = e.get("deep_convection_parametrized")
    dxs = grid_spacings_m(e.get("resolution"))
    classes = {_model_class(param, dx, rule) for dx in dxs} or {_model_class(param, None, rule)}
    return classes.pop() if len(classes) == 1 else "mixed"


# ---- copying: statements in the model's own words (derived.COPY_LIMIT)

def copied(it, out: dict) -> list[dict]:
    """The mentions whose statement copies a run of COPY_LIMIT or more words
    from the paper (numbers and run labels count once; the title and acronym
    names are allowed): [{n (1-based), run, words}]."""
    from . import derived
    grams = derived.ngrams(it.text())
    bad = []
    for n, m in enumerate((out or {}).get("mentions") or [], 1):
        run, words, _, _ = derived.longest_copy(m.get("statement", ""), grams, title=it.title or "")
        if run >= derived.COPY_LIMIT:
            bad.append({"n": n, "run": run, "words": words})
    return bad


from .copyfix import (MEANING_SCHEMA, MEANING_SYSTEM, REWRITE_SCHEMA, candidate_rewrites,  # noqa: E402,F401
                      meaning_job, merge_rewrites, numbers, rephrase_job, rewrite_problem)


def drop_copied(it, recs: list[dict], quotes: list[dict]) -> tuple[list[dict], list[dict], int]:
    """Records still copying after the re-ask are left out (their quote too)."""
    bad = {b["n"] - 1 for b in copied(it, {"mentions": recs})}
    keep = [r for i, r in enumerate(recs) if i not in bad]
    ids = {r["id"] for r in keep}
    return keep, [q for q in quotes if q["mention"] in ids], len(bad)
