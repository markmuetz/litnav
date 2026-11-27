#!/usr/bin/env python3
"""The copy check and the rewrite follow-ups for claim mentions.

Standard library only (Python 3.9+): litnav imports it, and it is shipped
beside run_jobs.py so a cluster batch runs the follow-ups itself, in the same
job, rather than in a second batch after another wait in the queue.

A statement that copies COPY_LIMIT or more words from the text the model was
sent is rewritten on its own (a "-r" job continuing the same conversation),
and each usable rewrite's meaning is checked by a fresh call (a "-c" job).
`next_job` gives the next step of that chain for a job and its results, so
a runner can start it live or rebuild it after a restart.
"""
from __future__ import annotations

import json
import re

COPY_LIMIT = 12                  # words: a longer run copied from the paper is rejected


_FUNCTION = {"of", "the", "on", "and", "for", "in", "at", "to", "a", "an", "with", "from", "by"}


def _fold(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", text)
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


_TOKEN = re.compile(r"[^\W_]+(?:[.,\-\u2212\u2013/][^\W_]+)*")


def words(text: str) -> list[str]:
    """Lower-case words, accents folded; letters of any script and digits. A
    token with a digit in it is one word however it is punctuated, so a number
    ('2.5', '2.5-3.0', '−1') or a run label ('1.5F', 'E63-SMCM') counts once;
    hyphenated words ('high-resolution') are split."""
    out = []
    for m in _TOKEN.finditer(_fold(text)):
        t = m.group()
        if any(c.isdigit() for c in t):
            out.append(t.replace("\u2212", "-").replace("\u2013", "-"))
        else:
            out += re.findall(r"[^\W_]+", t)
    return out


def repair(text: str) -> str:
    """Undo PDF line-end hyphenation: 'convec-\ntion' and 'convec- tion' -> 'convection'
    (not before and/or/to, as in 'pre- and post-')."""
    text = re.sub(r"(\w)-[ \t]*\n\s*(\w)", r"\1\2", text)
    return re.sub(r"(\w)- (?!(?:and|or|to|vs)\b)(?=[a-z])", r"\1", text)


def ngrams(text: str, n: int = 6) -> set:
    w = words(repair(text))
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def allowed_spans(text: str, title: str = "") -> set[int]:
    """Word positions in `text` that may match the paper: the paper's title, and
    a name written out before its acronym, e.g. 'Dynamics of the Atmospheric
    general circulation Modeled On Non-hydrostatic Domains (DYAMOND)'."""
    w = words(text)
    out: set[int] = set()
    tw = words(title)
    if len(tw) >= 4:
        tgrams = {" ".join(tw[i:i + 4]) for i in range(len(tw) - 3)}
        for i in range(len(w) - 3):
            if " ".join(w[i:i + 4]) in tgrams:
                out.update(range(i, i + 4))
    for m in re.finditer(r"\(([A-Z][A-Za-z0-9-]{1,11})\)", text):
        acr = [c for c in _fold(m.group(1)) if c.isalnum()]
        before = words(text[:m.start()])
        end = len(before)
        for k in range(min(end, 3 * len(acr)), 0, -1):       # the longest name the acronym spells
            span = before[end - k:]
            if span[0][0] != acr[0] or span[0] in _FUNCTION:
                continue
            joined, i = "".join(span), 0
            for c in joined:
                if i < len(acr) and c == acr[i]:
                    i += 1
            if i == len(acr):
                out.update(range(end - k, end))
                break
    return out


def longest_copy(out_text: str, paper_grams: set, n: int = 6, title: str = "") -> tuple[int, str, int, int]:
    """(longest run of words copied from the paper, that run, windows of n words
    found in the paper, windows in all). Allowed spans (allowed_spans) break runs
    and are not counted."""
    w = words(out_text)
    ok = allowed_spans(out_text, title)
    best = run = shared = total = 0
    best_end = 0
    for i in range(len(w) - n + 1):
        if any(j in ok for j in range(i, i + n)):
            run = 0
            continue
        total += 1
        if " ".join(w[i:i + n]) in paper_grams:
            shared += 1
            run = run + 1 if run else n
            if run > best:
                best, best_end = run, i + n
        else:
            run = 0
    return best, " ".join(w[best_end - best:best_end]) if best else "", shared, total


# A statement that copies the paper is rewritten on its own, not by asking for
# the whole reply again (which can change mentions that were fine). The call
# continues the conversation that wrote it, so the model has the same passages
# for context. A rewrite is used only if it keeps the statement's numbers and a
# second call (MEANING_SYSTEM) finds it says the same as the original; anything
# else keeps the original, which production then drops (drop_copied).

REWRITE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["rewrites"], "properties": {
    "rewrites": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                            "required": ["n", "statement"],
                                            "properties": {"n": {"type": "integer"},
                                                           "statement": {"type": "string"}}}}}}
MEANING_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["checks"], "properties": {
    "checks": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                          "required": ["n", "verdict", "reason"],
                                          "properties": {"n": {"type": "integer"},
                                                         "verdict": {"enum": ["same", "different"]},
                                                         "reason": {"type": "string"}}}}}}
MEANING_SYSTEM = (
    "You check rewritten statements for a claim graph. Each pair has an original statement (A), taken from "
    "the passages given, and a rewrite of it in other words (B). For each pair: same, if B says exactly "
    "what A says: the same numbers and units, the same direction of each effect, the same conditions, the "
    "same strength (hedging), nothing added and nothing left out; otherwise different. Judge from the "
    "passages, not from what you know. Give a short reason. Reply with JSON only.")


def _facts(m: dict) -> str:
    v = m.get("value") or {}
    val = " ".join(str(v.get(k) or "") for k in ("value", "units")).strip()
    bits = [f"claim type {m.get('claim_type')}", f"hedging {m.get('hedging')}"]
    if m.get("conditions"):
        bits.append(f"conditions: {m['conditions']}")
    if val:
        bits.append(f"value: {val}")
    return "; ".join(bits)


def rephrase_job(job: dict, out: dict, bad: list[dict]) -> dict:
    """The same call, continued: the model sees its reply, which statements
    copied which words, and rewrites only those (REWRITE_SCHEMA)."""
    ms = (out or {}).get("mentions") or []
    lines = "\n".join(
        f"- mention {b['n']}: \"{ms[b['n'] - 1].get('statement', '')}\"\n  copied words: \"{b['words']}\"\n"
        f"  its record: {_facts(ms[b['n'] - 1])}" for b in bad if 0 < b["n"] <= len(ms))
    note = ("These statements copy runs of words from the passages:\n" + lines + "\n\n"
            "Rewrite only these statements, in your own words, using the passages above for context. Each must "
            "say exactly what it said before: the same numbers with their units, the same direction of each "
            "effect, the same conditions, and the same hedging, matching its record. Do not add or drop "
            "anything. Names of models, datasets, places and campaigns may stay. Reply with JSON only: "
            "{\"rewrites\": [{\"n\": <mention number>, \"statement\": \"...\"}]}")
    return {**job, "id": f"{job['id']}-r", "rephrased": True, "rewrite": [b["n"] for b in bad],
            "schema": REWRITE_SCHEMA,
            "messages": job["messages"] + [{"role": "assistant", "content": json.dumps(out, ensure_ascii=False)},
                                           {"role": "user", "content": note}]}


_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def numbers(text: str) -> list[str]:
    """The numbers in a statement, normalised (1,000 and 1000 are the same)."""
    return sorted(n.replace(",", "") for n in _NUM.findall(text or ""))


def rewrite_problem(old: str, new: str) -> str:
    """Why a rewrite can't be used, checked by code; '' if it can."""
    if not (new or "").strip():
        return "empty"
    if numbers(old) != numbers(new):
        return f"numbers changed: {numbers(old)} -> {numbers(new)}"
    if new.strip() == old.strip():
        return "unchanged"
    return ""


def candidate_rewrites(out: dict, again: dict, asked: list[int]) -> dict[int, str]:
    """Rewrites that pass the code checks, by mention number."""
    ms = (out or {}).get("mentions") or []
    got = {}
    for r in (again or {}).get("rewrites") or []:
        n = r.get("n")
        if isinstance(n, int) and n in asked and 0 < n <= len(ms) and n not in got:
            if not rewrite_problem(ms[n - 1].get("statement", ""), r.get("statement", "")):
                got[n] = r["statement"].strip()
    return got


def meaning_job(job: dict, out: dict, rewrites: dict[int, str]) -> dict:
    """A fresh call that checks each rewrite against its original, with the
    same passages the original was written from."""
    ms = out["mentions"]
    pairs = "\n".join(f"{n}. A: {ms[n - 1].get('statement', '')}\n   B: {t}" for n, t in sorted(rewrites.items()))
    user = job["messages"][-1]["content"] if job["messages"][-1]["role"] == "user" else ""
    for m in job["messages"]:
        if m["role"] == "user":
            user = m["content"]
            break
    return {**job, "id": f"{job['id']}-c", "meaning_check": sorted(rewrites), "schema": MEANING_SCHEMA,
            "messages": [{"role": "system", "content": MEANING_SYSTEM},
                         {"role": "user", "content": f"{user}\n\nPairs to check:\n{pairs}\n\nReply with JSON only."}]}


def merge_rewrites(out: dict, again: dict, asked: list[int], checks: dict | None,
                   checked: list[int] | None = None) -> tuple[dict, dict]:
    """The reply with the rewrites the meaning check called 'same' put in
    place; every other mention exactly as it was. Returns (output, counts).
    `checked`: the numbers sent to the check (rewrites that still copied were not)."""
    cand = candidate_rewrites(out, again, asked)
    if checked is not None:
        cand = {n: t for n, t in cand.items() if n in checked}
    verdict = {c.get("n"): c.get("verdict") for c in (checks or {}).get("checks") or []}
    use = {n: t for n, t in cand.items() if verdict.get(n) == "same"}
    ms = [dict(m) for m in (out or {}).get("mentions") or []]
    for n, t in use.items():
        ms[n - 1] = {**ms[n - 1], "statement": t, "rewritten": True}
    return {**(out or {}), "mentions": ms}, {"asked": len(asked), "candidates": len(cand), "used": len(use)}


# ---- the chain, for a runner

RUNS_SLOT = "{{RUNS}}"


def runs_table(runs: list[dict]) -> str:
    """A paper's experiments table, one line each, for a section call's prompt."""
    lines = []
    for r in runs or []:
        name = r.get("name") or r.get("run_name") or ""
        if r.get("kind") not in ("observational_analysis", "model_run", None):   # an induced index (G0 prompt)
            lines.append("- " + json.dumps(r, ensure_ascii=False))
            continue
        if r.get("kind") == "observational_analysis":
            ds = ", ".join(" ".join(x for x in (d.get("name"), d.get("version")) if x) for d in r.get("datasets") or [])
            bits = [x for x in (ds, r.get("method"), r.get("region"), r.get("period")) if x]
            lines.append(f"- observational analysis '{name}': " + "; ".join(bits))
            continue
        comps = "; ".join(
            f"{c.get('kind', '').replace('_', ' ')}: "
            + ({"off": "off", "not_stated": "not stated"}.get(c.get("state"), c.get("scheme") or "used"))
            for c in r.get("components") or [])
        if "deep_convection" in r:              # the lean table (v7)
            comps = "deep convection: " + {"off": "off", "not_stated": "not stated"}.get(r["deep_convection"],
                                                                                       "used")
        head = ", ".join(x for x in (r.get("model"), r.get("configuration"), r.get("horizontal_grid_spacing"),
                                     r.get("domain")) if x)
        lines.append(f"- model run '{name}': {head}" + (f"; {comps}" if comps else ""))
    return "\n".join(lines) or "(no experiments found in the methods)"


def fill(job: dict, results: dict) -> dict:
    """A job that waits for another ("after") with that job's runs table put
    in its prompt; any other job unchanged."""
    after = job.get("after")
    if not after:
        return job
    r = results.get(after) or {}
    out = r.get("output") if r.get("ok") else None
    t = (out.get("experiments", out.get("runs")) if isinstance(out, dict) else None)
    table = runs_table(t if isinstance(t, list) else None)
    msgs = [{**m, "content": m["content"].replace(RUNS_SLOT, table)} if m["role"] == "user" else m
            for m in job["messages"]]
    return {**job, "messages": msgs}


def ready(job: dict, results: dict) -> bool:
    """A job can start: it waits for nothing, or what it waits for has finished (ok or not)."""
    return not job.get("after") or job["after"] in results

def statements_copied(statements: list[str], grams: set, title: str = "") -> list[dict]:
    """[{n (1-based), run, words}] for statements copying COPY_LIMIT+ words."""
    bad = []
    for n, s in enumerate(statements, 1):
        run, w, _, _ = longest_copy(s or "", grams, title=title)
        if run >= COPY_LIMIT:
            bad.append({"n": n, "run": run, "words": w})
    return bad


def sent_text(job: dict) -> str:
    """What the model was sent besides the instructions: what it could copy."""
    return "\n".join(m["content"] for m in job["messages"] if m["role"] == "user")


# ---- summaries: one re-ask of the whole reply (a summary is read as a whole)

def summary_parts(d: dict) -> list[str]:
    return [d.get("topic", ""), d.get("summary", "")] + [x.get("statement", "") for x in d.get("key_items") or []]


def summary_copy_error(d: dict, grams: set, title: str = "") -> str:
    """The refusal for a summary that copies (as litnav words it), or ''."""
    best, phrase = 0, ""
    for part in summary_parts(d):
        b, ph, _, _ = longest_copy(part or "", grams, title=title)
        if b > best:
            best, phrase = b, ph
    if best >= COPY_LIMIT:
        return f"copies a run of {best} words from the paper (limit {COPY_LIMIT - 1}): \"{phrase}\""
    return ""


def rephrase_note(err: str) -> str:
    """The message that re-asks a summary refused for copying."""
    return (f"Your summary was refused: it {err.split(': ', 1)[0]}. The copied words: "
            f"{err.split(': ', 1)[1] if ': ' in err else ''}. Write the whole JSON again, every field in your own "
            "words: no run of words from the paper, apart from names. Reply with JSON only.")


def summary_rephrase_job(job: dict, out: dict, err: str) -> dict:
    """The same summary job, asked once more after a refusal for copying: the
    model sees its reply and the copied words."""
    return {**job, "id": f"{job['id']}-r", "rephrased": True,
            "messages": job["messages"] + [{"role": "assistant", "content": json.dumps(out, ensure_ascii=False)},
                                           {"role": "user", "content": rephrase_note(err)}]}


def next_job(job: dict, results: dict) -> dict | None:
    """The next follow-up for a job that asks for them (job["followup"] is
    "mentions" or "summary"), given the results so far by id; None when there
    is nothing (left) to do."""
    if job.get("followup") == "summary":
        if job.get("rephrased") or f"{job['id']}-r" in results:
            return None
        first = results.get(job["id"]) or {}
        out = first.get("output")
        if not (first.get("ok") and isinstance(out, dict)):
            return None
        err = summary_copy_error(out, ngrams(sent_text(job)), job.get("title", ""))
        return summary_rephrase_job(job, out, err) if err else None
    if job.get("followup") != "mentions" or job.get("rewrite") or job.get("meaning_check"):
        return None
    job = fill(job, results)
    first = results.get(job["id"]) or {}
    out = first.get("output")
    if not (first.get("ok") and isinstance(out, dict)):
        return None
    ms = out.get("mentions") or []
    grams = ngrams(sent_text(job))
    again = results.get(f"{job['id']}-r")
    if again is None:
        bad = statements_copied([m.get("statement", "") for m in ms], grams, job.get("title", ""))
        return rephrase_job(job, out, bad) if bad else None
    if f"{job['id']}-c" in results or not (again.get("ok") and isinstance(again.get("output"), dict)):
        return None
    asked = again.get("rewrite") or [b["n"] for b in statements_copied(
        [m.get("statement", "") for m in ms], grams, job.get("title", ""))]
    cand = candidate_rewrites(out, again["output"], asked)
    still = {b["n"] for b in statements_copied([cand.get(n, "") for n in range(1, max(cand, default=0) + 1)],
                                              grams, job.get("title", ""))}
    cand = {n: t for n, t in cand.items() if n not in still}
    return meaning_job(job, out, cand) if cand else None
