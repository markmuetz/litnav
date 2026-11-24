"""Model-derived files: per-paper summaries and the theme report.

Every piece of library text that goes to a model passes through `outbound()`,
which checks each item against `send_out` (and so against confidential items
and the library's `licensed_to_models` answer) and raises net.Refused. The
Anthropic SDK and the `claude` CLI are ways out of the machine that
net.Client never sees, so this is their gate.

Summaries (map step, one per paper) default to Haiku. Two routes:
  api          Message Batches (half price). Submit, exit, collect on a later
               run: `litnav summarize` again picks the batch up.
  claude-code  the user's Claude Code login, one `claude -p` per paper, in
               order. Slower than a batch (5-20 s a paper; a few hours for a
               thousand papers). Each summary is
               written as it finishes, so an interrupted run just resumes.

summary.json (prompt v2, Mark 23 Nov 2026; v1 had no key_items and 2-4 sentences):
  {"topic", "summary" (200-250 words), "keywords",
   "key_items": [{kind (method / finding / conclusion / limitation), statement,
                  numbers, conditions, hedging, chunks (1-2 passage ids),
                  check (the local model's verification, `litnav check-summaries`)}],
   "provenance": {model, via, prompt_version, text_sha256, text_source,
                  chars_sent, truncated, copied_run}}
Summaries stand in for the paper wherever its text may not go (Claude, ask,
agents), so they are longer than a triage note, every key item names the
passages it rests on (so it can be checked), and none may copy a run of
COPY_LIMIT words from the paper: one that does is rejected and redone.
No date in provenance: it would turn every --force into a whole-corpus diff.
A summary is current when text sha, model and prompt version all match.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile

from . import sharing as S
from .net import Refused
from .copyfix import (COPY_LIMIT, _FUNCTION, _fold, allowed_spans, longest_copy, ngrams,  # noqa: F401
                      rephrase_note, repair, words)
from .util import atomic_write, sha256_file

SUMMARY_MODEL = "claude-haiku-5-5"
CLAUDE_CODE_SUMMARY_MODEL = "haiku"
THEMES_MODEL = "claude-opus-5-5"
PROMPT_VERSION = 2               # v2: 200-250 words, key items with passage ids
MAX_TEXT_CHARS = 150_000
SUMMARY_MAX_TOKENS = 8000        # Haiku thinks by default; thinking counts against this

ITEM_KINDS = ["method", "finding", "conclusion", "limitation"]
HEDGING = ["strong", "moderate", "weak"]


def schema(ids: list[str] | None = None) -> dict:
    """The summary schema; with `ids`, key items may name only those passages."""
    chunk = {"type": "string", **({"enum": list(ids)} if ids else {})}
    return {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "One concise sentence: what the paper is about."},
            "summary": {"type": "string", "description": (
                "200-250 words of prose: the question and why it matters; the data and methods; the "
                "main findings, with numbers; the conclusions; the limitations or caveats.")},
            "keywords": {"type": "array", "items": {"type": "string"},
                         "description": "4-8 lower-case topical keywords."},
            "key_items": {"type": "array", "minItems": 8, "maxItems": 15, "items": {
                "type": "object", "properties": {
                    "kind": {"type": "string", "enum": ITEM_KINDS},
                    "statement": {"type": "string"},
                    "numbers": {"type": "array", "items": {"type": "string"}},
                    "conditions": {"type": "string"},
                    "hedging": {"type": "string", "enum": HEDGING},
                    "chunks": {"type": "array", "minItems": 1, "maxItems": 2, "items": chunk}},
                "required": ["kind", "statement", "numbers", "conditions", "hedging", "chunks"],
                "additionalProperties": False}},
        },
        "required": ["topic", "summary", "keywords", "key_items"],
        "additionalProperties": False,
    }


SCHEMA = schema()

SYSTEM = (
    "You summarize academic papers for a personal literature database. People and models use the "
    "summary in place of the paper where its text can't be shared, so it must be accurate and "
    "specific. The paper's text, extracted from a PDF and possibly noisy (running heads, OCR "
    "errors), is given as numbered passages. Do not invent details. Write in your own words: never "
    "copy a run of words from the paper.\n"
    "- topic: one sentence, what the paper is about.\n"
    "- summary: 200-250 words of prose: the question and why it matters; the data and methods; the "
    "main findings, with their numbers; the conclusions; and the limitations or caveats the paper "
    "states.\n"
    "- keywords: 4-8, lower case.\n"
    "- key_items: 8-15 of the paper's key methods, findings, conclusions and limitations, the most "
    "important first. Each statement is one standalone sentence a reader understands without the "
    "paper: name the model, region, period and quantity; keep numbers with units and the direction "
    "of each effect. numbers: the numbers it states, as written. conditions: where and when it "
    "holds ('' if general). hedging: how firmly the paper states it: strong (shows, demonstrates), "
    "moderate (suggests, indicates), weak (may, could). chunks: the ids of the one or two passages "
    "that state it. Include at least one limitation if the paper states any.")
INSTRUCTION = "Summarize this paper.\n\nPaper: {title} ({year})\n\nPassages:\n{text}"
OCR_NOTE = ("\n\nThe text came from OCR: equations and figure captions are unreliable, so "
            "do not rely on them.")


# ---- the gate

def outbound(lib, items, what="text"):
    """Raise Refused unless every item may have its text sent to a model."""
    for it in items:
        if not it.allowed(S.SEND_OUT):
            why = "the library's licensed_to_models setting" if it.sharing == "licensed" else it.sharing
            raise Refused(f"{it.key}: {what} not sent to a model ({why})")


# ---- selection and writing

def text_for(it, max_chars: int = MAX_TEXT_CHARS) -> tuple[str, bool]:
    t = it.text().strip()
    return t[:max_chars], len(t) > max_chars


def passages(it, max_chars: int = MAX_TEXT_CHARS) -> tuple[list[dict], bool]:
    """The paper as numbered passages (the index's chunk ids, computed the same
    way, so no index is needed), in order, within max_chars; acknowledgements
    left out. Returns (passages, truncated)."""
    from . import chunks as C, sections as Sec
    manual = it.meta.get("files", {}).get("sections") == "manual"
    cs = [c for c in C.chunk(it.key, Sec.parse(it.text(), manual=manual)) if c.kind != "ack"]
    out, used = [], 0
    for c in cs:
        if used >= max_chars:
            return out, True
        t = c.text[:max_chars - used]
        used += len(t)
        out.append({"id": c.id, "section": c.section_title or c.kind, "text": t})
    return out, False


def passage_body(ps: list[dict]) -> str:
    return "\n\n".join(f"[{p['id']}] ({p['section']})\n{p['text']}" for p in ps)


def summary_input(it, max_chars: int = MAX_TEXT_CHARS) -> dict:
    ps, trunc = passages(it, max_chars)
    ids = [p["id"] for p in ps]
    return {"prompt": _prompt(it, passage_body(ps)), "ids": ids, "schema": schema(ids),
            "chars": sum(len(p["text"]) for p in ps), "truncated": trunc}


def is_current(it, model: str) -> bool:
    s = it.summary()
    if not s:
        return False
    p = s.get("provenance", {})
    if p.get("source") == "legacy litman summary":
        return True
    return (p.get("text_sha256") == sha256_file(it.text_path) and p.get("model") == model
            and p.get("prompt_version") == PROMPT_VERSION)


def select(lib, *, keys=None, tag=None, force=False, stale=False, limit=None, model=SUMMARY_MODEL,
           purpose=S.SEND_OUT):
    """Items to summarize: have text, may go to the model (`send_out`, or
    `local_model` for a model the owner runs), and lack a summary (or, with
    --stale, whose text changed since; with --force, all)."""
    if keys:
        items = [lib.get(k) for k in keys]
        if purpose == S.SEND_OUT:
            outbound(lib, items)
        else:
            for it in items:
                if not it.allowed(purpose):
                    raise Refused(f"{it.key}: text not sent to a model ({it.sharing})")
    else:
        items = list(lib.items(purpose=purpose, tag=tag))
    out = []
    for it in items:
        if not it.has_text:
            continue
        s = it.summary()
        if force or not s:
            out.append(it)
        elif stale and s.get("provenance", {}).get("text_sha256") not in (None, sha256_file(it.text_path)):
            out.append(it)
    return out[:limit] if limit else out


def provenance(it, *, model, via, chars, truncated) -> dict:
    return {"model": model, "via": via, "prompt_version": PROMPT_VERSION,
            "text_sha256": sha256_file(it.text_path),
            "text_source": it.meta.get("files", {}).get("text_source", ""),
            "chars_sent": chars, "truncated": truncated}


def valid(d) -> bool:
    items = d.get("key_items") if isinstance(d, dict) else None
    return (isinstance(d, dict) and isinstance(d.get("topic"), str) and d["topic"].strip()
            and isinstance(d.get("summary"), str) and d["summary"].strip()
            and isinstance(d.get("keywords"), list)
            and all(isinstance(k, str) for k in d["keywords"])
            and isinstance(items, list) and len(items) >= 1
            and all(isinstance(x, dict) and isinstance(x.get("statement"), str) and x["statement"].strip()
                    and x.get("kind") in ITEM_KINDS and isinstance(x.get("chunks"), list) for x in items))


# ---- copying: no run of COPY_LIMIT words from the paper

def copied_run(out_text: str, paper_grams: set, n: int = 6, title: str = "") -> int:
    """Longest run of words copied from the paper (0 if under n)."""
    return longest_copy(out_text, paper_grams, n, title)[0]


def _parts(d: dict) -> list[str]:
    return [d.get("topic", ""), d.get("summary", "")] + [x.get("statement", "") for x in d.get("key_items") or []]


def copy_stats(it, d: dict) -> dict:
    """The longest copied run (and its words), and the share of the summary's
    6-word sequences found in the paper (logged, no threshold)."""
    grams = ngrams(it.text())
    best, phrase, shared, total = 0, "", 0, 0
    for part in _parts(d):
        b, ph, s_, t = longest_copy(part, grams, title=it.title or "")
        shared, total = shared + s_, total + t
        if b > best:
            best, phrase = b, ph
    return {"copied_run": best, "copied_words": phrase,
            "shared_6grams": round(shared / total, 3) if total else 0.0}


def summary_copied_run(it, d: dict) -> int:
    return copy_stats(it, d)["copied_run"]


def check(it, d) -> str | None:
    """Why a model's summary can't be written (None if it can)."""
    if not valid(d):
        return "summary did not match the schema"
    st = copy_stats(it, d)
    if st["copied_run"] >= COPY_LIMIT:
        return f"copies a run of {st['copied_run']} words from the paper (limit {COPY_LIMIT - 1}): \"{st['copied_words']}\""
    return None


def write_summary(it, data: dict, prov: dict) -> bool:
    """Write summary.json in a stable layout; False if nothing changed."""
    items = [{"kind": x["kind"], "statement": x["statement"].strip(),
              "numbers": [str(n) for n in x.get("numbers") or []], "conditions": (x.get("conditions") or "").strip(),
              "hedging": x.get("hedging"), "chunks": list(x.get("chunks") or [])}
             for x in data.get("key_items") or []]
    st = copy_stats(it, data)
    prov = {**prov, "copied_run": st["copied_run"], "shared_6grams": st["shared_6grams"]}
    d = {"topic": data["topic"].strip(), "summary": data["summary"].strip(),
         "keywords": [k.strip() for k in data["keywords"] if k.strip()], "key_items": items, "provenance": prov}
    new = json.dumps(d, indent=2, ensure_ascii=False) + "\n"
    if it.summary_path.is_file() and it.summary_path.read_text("utf-8") == new:
        return False
    atomic_write(it.summary_path, new)
    return True


def _prompt(it, body):
    note = OCR_NOTE if it.meta.get("files", {}).get("text_source", "").startswith("ocr") else ""
    return INSTRUCTION.format(title=it.title, year=it.year, text=body) + note


# ---- route 1: the Anthropic API (Message Batches)

def state_path(lib):
    return lib.index_dir / "summarize_batch.json"


def _params(it, inp, model):
    return {"model": model, "max_tokens": SUMMARY_MAX_TOKENS, "system": SYSTEM,
            "messages": [{"role": "user", "content": inp["prompt"]}],
            "output_config": {"format": {"type": "json_schema", "schema": inp["schema"]}, "effort": "low"}}


def _parse_message(msg) -> tuple[dict | None, str | None]:
    if msg.stop_reason in ("max_tokens", "refusal"):
        return None, f"stopped: {msg.stop_reason}"
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"bad JSON: {e}"
    return d, None


def api_submit(lib, items, model=SUMMARY_MODEL, log=print) -> list[str]:
    """Summarize the first item synchronously (catches parameter errors now,
    not hours later), then submit the rest as one batch and return."""
    import anthropic
    outbound(lib, items)
    if state_path(lib).exists():
        raise ValueError(f"a batch is already pending ({state_path(lib)}); run `litnav summarize` "
                         "to collect it first")
    client = anthropic.Anthropic()
    written = []
    first, rest = items[0], items[1:]
    inp = summary_input(first)
    msg = client.messages.create(**_params(first, inp, model))
    d, err = _parse_message(msg)
    err = err or check(first, d)
    if err:
        raise RuntimeError(f"{first.key}: {err}")
    if write_summary(first, d, provenance(first, model=model, via="api", chars=inp["chars"],
                                          truncated=inp["truncated"])):
        written.append(first.key)
    log(f"{first.key}: summarized (test request)")
    if not rest:
        return written
    reqs, ids = [], {}
    for i, it in enumerate(rest):
        inp = summary_input(it)
        cid = f"p{i:05d}"
        ids[cid] = {"key": it.key, "sha": sha256_file(it.text_path), "chars": inp["chars"],
                    "truncated": inp["truncated"]}
        reqs.append({"custom_id": cid, "params": _params(it, inp, model)})
    batch = client.messages.batches.create(requests=reqs)
    atomic_write(state_path(lib), json.dumps({"batch_id": batch.id, "model": model, "items": ids},
                                             indent=1))
    log(f"submitted batch {batch.id} for {len(reqs)} papers. Batches usually finish within an "
        f"hour (at most 24 h); run `litnav summarize` again to collect it.")
    return written


def api_collect(lib, log=print) -> tuple[list[str], bool]:
    """Collect a pending batch. Returns (keys written, finished?)."""
    import anthropic
    st = json.loads(state_path(lib).read_text())
    client = anthropic.Anthropic()
    b = client.messages.batches.retrieve(st["batch_id"])
    if b.processing_status != "ended":
        rc = b.request_counts
        log(f"batch {b.id} still {b.processing_status}: {rc.succeeded} done, {rc.processing} "
            f"processing, {rc.errored} errored. Run again later.")
        return [], False
    written, failed = [], 0
    for r in client.messages.batches.results(st["batch_id"]):
        info = st["items"].get(r.custom_id)
        if not info:
            continue
        key = lib.resolve(info["key"])
        if not key:
            log(f"{info['key']}: no longer in the library; result dropped")
            continue
        it = lib.get(key)
        # Re-check: the item may have become confidential, or its text changed, since submission.
        if not it.allowed(S.SEND_OUT) or not it.has_text or sha256_file(it.text_path) != info["sha"]:
            log(f"{key}: sharing or text changed since submission; result dropped")
            continue
        if r.result.type != "succeeded":
            failed += 1
            log(f"{key}: {r.result.type}; left for the next run")
            continue
        d, err = _parse_message(r.result.message)
        err = err or check(it, d)
        if err:
            failed += 1
            log(f"{key}: {err}; left for the next run")
            continue
        prov = {"model": st["model"], "via": "api-batch", "prompt_version": PROMPT_VERSION,
                "text_sha256": info["sha"],
                "text_source": it.meta.get("files", {}).get("text_source", ""),
                "chars_sent": info["chars"], "truncated": info["truncated"]}
        if write_summary(it, d, prov):
            written.append(key)
    state_path(lib).unlink()
    log(f"batch collected: {len(written)} summaries written, {failed} failed")
    return written, True


# ---- route 2: Claude Code

class Stop(Exception):
    """A failure that will repeat for every paper (auth, usage limit): stop the run."""


_FATAL = re.compile(r"usage limit|rate limit|not logged in|log ?in|authenticat|credit|quota|"
                    r"invalid api key", re.I)


def claude_code_args(model: str, schema: dict | None = None, system: str = SYSTEM) -> list[str]:
    """A `claude -p` that runs no tools, loads none of the user's settings,
    memory or commands, and keeps no transcript of the text it was sent."""
    cmd = ["claude", "-p", "--model", model, "--output-format", "json",
           "--no-session-persistence", "--setting-sources", "", "--disable-slash-commands",
           "--tools", "", "--strict-mcp-config", "--system-prompt", system]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    return cmd


def run_claude(cmd: list[str], prompt: str, timeout: int = 600) -> dict:
    if not shutil.which("claude"):
        raise Stop("the claude CLI is not installed")
    with tempfile.TemporaryDirectory(prefix="litnav-cc-") as td:
        cmd = cmd[:2] + ["Follow the instructions in the input."] + cmd[2:]
        try:
            # The text goes on stdin, not argv: argv is visible to other processes and capped.
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=td,
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"timed out after {timeout} s")
    try:
        env = json.loads(p.stdout)
    except json.JSONDecodeError:
        msg = (p.stderr or p.stdout or "no output").strip()[:300]
        if _FATAL.search(msg):
            raise Stop(msg)
        raise RuntimeError(msg)
    if env.get("is_error"):
        msg = str(env.get("result") or env.get("subtype"))[:300]
        if _FATAL.search(msg):
            raise Stop(msg)
        raise RuntimeError(msg)
    return env


def claude_code_summarize(lib, items, model=CLAUDE_CODE_SUMMARY_MODEL, jobs: int = 1, log=print) -> list[str]:
    """One `claude -p` per paper, `jobs` at a time. A failure that will repeat
    (auth, usage limit) stops the whole run; finished summaries are kept."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    outbound(lib, items)
    written, stop, lock = [], threading.Event(), threading.Lock()
    total = len(items)

    def one(n_it):
        n, it = n_it
        if stop.is_set():
            return
        outbound(lib, [it])          # sharing may change during a long run
        inp = summary_input(it)
        try:
            env = run_claude(claude_code_args(model, inp["schema"]),
                             "Summarize the paper whose text follows.\n\n" + inp["prompt"])
        except Stop as e:
            if not stop.is_set():
                stop.set()
                with lock:
                    log(f"stopping: {e}")
            return
        except RuntimeError as e:
            with lock:
                log(f"[{n}/{total}] {it.key}: failed ({e}); left for the next run")
            return
        d = env.get("structured_output")
        err = check(it, d)
        if err and err.startswith("copies"):           # ask once more, with the copied words
            try:
                env = run_claude(claude_code_args(model, inp["schema"]),
                                 "Summarize the paper whose text follows.\n\n" + inp["prompt"]
                                 + "\n\nA previous summary of this paper:\n" + json.dumps(d, ensure_ascii=False)
                                 + "\n\n" + rephrase_note(err))
                d = env.get("structured_output")
                err = check(it, d)
            except (Stop, RuntimeError) as e:
                err = f"{err}; asking again failed ({e})"
        if err:
            with lock:
                log(f"[{n}/{total}] {it.key}: {err}; left for the next run")
            return
        real = next(iter(env.get("modelUsage") or {}), model)
        changed = write_summary(it, d, provenance(it, model=real, via="claude-code", chars=inp["chars"],
                                                  truncated=inp["truncated"]))
        with lock:
            if changed:
                written.append(it.key)
            log(f"[{n}/{total}] {it.key}: summarized")

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        list(ex.map(one, enumerate(items, 1)))
    return written


# ---- themes (reduce step: one call over every summary)

THEMES_EFFORT = "high"
THEMES_MAX_TOKENS = 64000
CLAUDE_CODE_THEMES_MODEL = "opus"

# Keys as they appear in a report: `key` (what models emit), [key] or [@key].
_REPORT_KEY = re.compile(r"`([A-Za-z][\w.:-]*)`|\[@?([A-Za-z][\w.:-]*)\]")


def themes_path(lib):
    return lib.root / "derived" / "themes.md"


def keys_in_report(report: str) -> set[str]:
    return {a or b for a, b in _REPORT_KEY.findall(report)}


def theme_inputs(lib, tag=None) -> list[tuple[str, dict]]:
    """(key, summary) for papers whose summary may go to a model *now*: a
    summary written before a sharing change is filtered by today's level.
    Summaries are model-written, so `send_derived` decides, not `send_out`."""
    out = []
    for it in lib.items(purpose=S.SEND_DERIVED, tag=tag):
        s = it.summary()
        if s:
            out.append((it.key, s))
    return out


def _lines(summaries) -> str:
    return "\n".join(f"- [@{k}] {s.get('topic', '')} | keywords: {', '.join(s.get('keywords', []))}"
                     for k, s in summaries)


def themes_prompt(summaries, report: str | None = None) -> str:
    if report is None:
        return ("Below is a list of papers from a personal literature library, one per line, each "
                "with a one-line topic and keywords. Identify the major recurring themes across the "
                "collection. For each theme: a short numbered title, a one-sentence description, "
                "and the paper keys that belong to it, written as [@key]. A paper may appear under "
                "more than one theme. End with a short note on the overall shape of the "
                f"collection. Use Markdown.\n\nPapers ({len(summaries)}):\n{_lines(summaries)}")
    return ("Below is an existing theme report for a personal literature library, followed by "
            "papers added since it was written. Update the report to cover them.\n\nRules:\n"
            "- Return the COMPLETE updated report, not a diff or a summary of changes.\n"
            "- Keep existing themes verbatim: same numbering, titles, descriptions and wording. "
            "The only edit to an existing theme is appending new paper keys to its list.\n"
            "- Add each new paper to whichever existing themes fit. A paper may appear under "
            "more than one theme; one that fits nothing may be left out.\n"
            "- Create a new theme only if several new papers share a subject no theme covers. "
            "Append new themes at the end, continuing the numbering.\n"
            "- Write keys as [@key].\n"
            "- Leave the closing note unchanged unless the new papers change the picture.\n\n"
            f"EXISTING REPORT:\n{report}\n\nNEW PAPERS ({len(summaries)}):\n{_lines(summaries)}")


def themes_api(prompt: str, model=THEMES_MODEL, log=print) -> str:
    import anthropic
    client = anthropic.Anthropic()
    parts = []
    with client.messages.stream(model=model, max_tokens=THEMES_MAX_TOKENS,
                                thinking={"type": "adaptive"},
                                output_config={"effort": THEMES_EFFORT},
                                messages=[{"role": "user", "content": prompt}]) as stream:
        for t in stream.text_stream:
            parts.append(t)
        final = stream.get_final_message()
    if final.stop_reason == "max_tokens":
        log(f"warning: the report was cut off at max_tokens={THEMES_MAX_TOKENS}")
    return "".join(parts)


def themes_claude_code(prompt: str, model=CLAUDE_CODE_THEMES_MODEL) -> str:
    env = run_claude(claude_code_args(model, system="You write theme reports for a personal "
                                      "literature library. Reply with the report only."),
                     prompt, timeout=1800)
    return str(env.get("result", ""))
