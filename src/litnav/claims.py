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
The evidence_type and hedging values are v0: not yet settled.
"""
from __future__ import annotations

import re
import secrets

PROMPT_VERSION = 1
SCHEMA_VERSION = "0.1"          # v0 evidence_type and hedging values
GROUP_CHARS = 20_000            # passages per call: fits a 32k-token context with room to think
SKIP_KINDS = {"ack", "front"}   # not claims; the title and abstract go to every call anyway

CLAIM_TYPES = ["shown", "restated", "assumed", "speculation"]
EVIDENCE_TYPES = ["observations", "reanalysis", "les_crm", "km_scale", "parametrized_model", "theory", "mixed"]
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
    "without support in this paper). A claim followed by a citation is restated, not shown.\n"
    "- evidence_type (provisional list): observations, reanalysis, les_crm (large-eddy or "
    "cloud-resolving, limited area), km_scale (convection-permitting regional or global), "
    "parametrized_model (GCM or RCM with parametrized convection), theory, mixed. The evidence behind "
    "the claim; for a restated claim, the cited work's if the passage says, otherwise the closest.\n"
    "- experiment: the model, its resolution and the method, where the claim rests on one ('' if not).\n"
    "- conditions: the region, regime, period and resolution it holds for ('' where not stated).\n"
    "- hedging (provisional levels): strong (shows, demonstrates, is), moderate (suggests, indicates, "
    "likely), weak (may, could, possibly). hedge_words: the paper's own hedge, a few words ('' if none).\n"
    "- value: for a claim about one number, its value, units and uncertainty range low and high, as "
    "written; '' in each field if the claim has no single number or no range.\n"
    "Reply with JSON only.")


def schema(ids: list[str], max_items: int = 40) -> dict:
    s = {"type": "string"}
    return {"type": "object", "properties": {"mentions": {"type": "array", "maxItems": max_items, "items": {
        "type": "object", "properties": {
            "statement": s,
            "chunk": {"type": "string", "enum": ids},
            "quote": s,
            "source": {"type": "string", "enum": SOURCES},
            "claim_type": {"type": "string", "enum": CLAIM_TYPES},
            "evidence_type": {"type": "string", "enum": EVIDENCE_TYPES},
            "experiment": {"type": "object", "properties": {"model": s, "resolution": s, "method": s},
                           "required": ["model", "resolution", "method"], "additionalProperties": False},
            "conditions": {"type": "object", "properties": {"region": s, "regime": s, "period": s,
                                                            "resolution": s},
                           "required": ["region", "regime", "period", "resolution"],
                           "additionalProperties": False},
            "hedging": {"type": "string", "enum": HEDGING},
            "hedge_words": s,
            "value": {"type": "object", "properties": {"value": s, "units": s, "low": s, "high": s},
                      "required": ["value", "units", "low", "high"], "additionalProperties": False}},
        "required": ["statement", "chunk", "quote", "source", "claim_type", "evidence_type", "experiment",
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
                "claim_type": m.get("claim_type"), "evidence_type": m.get("evidence_type"),
                "experiment": m.get("experiment") or {}, "conditions": m.get("conditions") or {},
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
