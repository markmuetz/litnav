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

import re
import secrets

PROMPT_VERSION = 3              # v3: model evidence as facts (scheme, grid spacing, run name); code labels it
SCHEMA_VERSION = "0.3"
EVIDENCE_RULE = 1               # evidence_type() below; Mark, 23 Nov 2026
LES_MAX_M = 500                 # grid spacing at or below this, with no deep-convection scheme: les
MAX_ITEMS = 40                  # mentions a call; report counts calls that hit it
GROUP_CHARS = 20_000            # passages per call: fits a 32k-token context with room to think
SKIP_KINDS = {"ack", "front"}   # not claims; the title and abstract go to every call anyway

CLAIM_TYPES = ["shown", "restated", "assumed", "speculation"]
EVIDENCE_KINDS = ["observations", "reanalysis", "model", "theory", "mixed", "unknown"]     # what the model labels
EVIDENCE_TYPES = ["observations", "reanalysis", "les", "cpm", "parametrized_model", "theory", "mixed",
                  "unknown"]                                                             # the record's, by rule
YNU = ["yes", "no", "unknown"]
HEDGING = ["strong", "moderate", "weak"]
SOURCES = ["text", "figure", "table"]

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
    "- experiment, where the claim rests on a model run ('' or unknown if not, or not stated): the model; "
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
        "required": ["statement", "chunk", "quote", "source", "claim_type", "evidence", "experiment",
                     "conditions", "hedging", "hedge_words", "value"],
        "additionalProperties": False}}}, "required": ["mentions"], "additionalProperties": False}


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


def messages(it, ctx: str, group: list[dict]) -> list[dict]:
    body = "\n\n".join(f"[{r['id']}] ({r.get('section_title') or r['kind']}{', caption' if r['kind'] == 'caption' else ''})"
                       f"\n{r['text']}" for r in group)
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{ctx}\n\nPassages:\n{body}\n\nReply with JSON only."}]


def jobs(lib, it, *, budget: int = GROUP_CHARS, prefix: str = "mentions") -> list[dict]:
    """One model call per group of sections."""
    rows = chunks(lib, it)
    ctx = context(it, rows)
    out = []
    for n, g in enumerate(groups(rows, budget), 1):
        ids = [r["id"] for r in g]
        out.append({"id": f"{prefix}-{it.key}--g{n:02d}", "kind": "eval", "task": "mentions", "key": it.key,
                    "group": n, "schema": schema(ids), "messages": messages(it, ctx, g), "passages": ids,
                    "sections": sorted({r.get("section_title") or r["kind"] for r in g}),
                    "chars_sent": sum(len(r["text"]) for r in g)})
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


def records(key: str, replies: list[dict], chunk_info: dict, *, run: str = "") -> tuple[list[dict], list[dict]]:
    """Mention records and their passages (kept apart) from the replies of one
    paper's calls. chunk_info: chunk id -> {section_title, page}."""
    recs, quotes = [], []
    for out in replies:
        for m in (out or {}).get("mentions") or []:
            mid = new_id()
            ci = chunk_info.get(m.get("chunk"), {})
            recs.append({
                "id": mid, "type": "mention", "schema_version": SCHEMA_VERSION, "paper": key,
                "chunk": m.get("chunk"), "section": ci.get("section_title"), "page": ci.get("page"),
                "statement": m.get("statement", ""), "source": m.get("source"),
                "claim_type": m.get("claim_type"), "evidence_type": evidence_type(m),
                "evidence_rule": EVIDENCE_RULE, "evidence": m.get("evidence"),
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


# ---- evidence type, by rule (EVIDENCE_RULE 1; the thresholds live here, not in the prompt)

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


def _model_class(param: str | None, dx: float | None) -> str:
    if param == "yes":
        return "parametrized_model"
    if param == "no":
        if dx is None:
            return "unknown"
        return "les" if dx <= LES_MAX_M else "cpm"
    return "unknown"


def evidence_type(m: dict) -> str:
    """The record's evidence type from the model's facts (rule 1, Mark 23 Nov 2026):
    a deep-convection scheme -> parametrized_model, whatever the grid spacing;
    otherwise grid spacing <= 500 m -> les, else cpm (no upper bound: grey-zone
    runs keep their spacing and can be filtered); missing facts -> unknown.
    Several grid spacings that fall in different classes -> mixed."""
    ev = m.get("evidence")
    if ev != "model":
        return ev if ev in EVIDENCE_TYPES else "unknown"
    e = m.get("experiment") or {}
    param = e.get("deep_convection_parametrized")
    dxs = grid_spacings_m(e.get("resolution"))
    classes = {_model_class(param, dx) for dx in dxs} or {_model_class(param, None)}
    return classes.pop() if len(classes) == 1 else "mixed"
