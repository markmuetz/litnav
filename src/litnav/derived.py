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

summary.json:
  {"topic", "summary", "keywords", "provenance": {model, via, prompt_version,
   text_sha256, text_source, chars_sent, truncated}}
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
from .util import atomic_write, sha256_file

SUMMARY_MODEL = "claude-haiku-5-5"
CLAUDE_CODE_SUMMARY_MODEL = "haiku"
THEMES_MODEL = "claude-opus-5-5"
PROMPT_VERSION = 1
MAX_TEXT_CHARS = 150_000
SUMMARY_MAX_TOKENS = 8000        # Haiku thinks by default; thinking counts against this

SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "description": "One concise sentence: what the paper is about."},
        "summary": {"type": "string",
                    "description": "2-4 sentences covering aims, methods and key findings."},
        "keywords": {"type": "array", "items": {"type": "string"},
                     "description": "4-8 lower-case topical keywords."},
    },
    "required": ["topic", "summary", "keywords"],
    "additionalProperties": False,
}

SYSTEM = ("You summarize academic papers for a personal literature database. The text "
          "was extracted from a PDF and may be noisy (running heads, references, OCR "
          "errors). Be concise and factual; do not invent details.")
INSTRUCTION = "Summarize this paper.\n\nPaper text:\n{text}"
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

def text_for(it) -> tuple[str, bool]:
    t = it.text().strip()
    return t[:MAX_TEXT_CHARS], len(t) > MAX_TEXT_CHARS


def is_current(it, model: str) -> bool:
    s = it.summary()
    if not s:
        return False
    p = s.get("provenance", {})
    if p.get("source") == "legacy litman summary":
        return True
    return (p.get("text_sha256") == sha256_file(it.text_path) and p.get("model") == model
            and p.get("prompt_version") == PROMPT_VERSION)


def select(lib, *, keys=None, tag=None, force=False, stale=False, limit=None, model=SUMMARY_MODEL):
    """Items to summarize: have text, may be sent out, and lack a summary
    (or, with --stale, whose text changed since; with --force, all)."""
    if keys:
        items = [lib.get(k) for k in keys]
        outbound(lib, items)
    else:
        items = list(lib.items(purpose=S.SEND_OUT, tag=tag))
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


def provenance(it, *, model, via, text, truncated) -> dict:
    return {"model": model, "via": via, "prompt_version": PROMPT_VERSION,
            "text_sha256": sha256_file(it.text_path),
            "text_source": it.meta.get("files", {}).get("text_source", ""),
            "chars_sent": len(text), "truncated": truncated}


def valid(d) -> bool:
    return (isinstance(d, dict) and isinstance(d.get("topic"), str) and d["topic"].strip()
            and isinstance(d.get("summary"), str) and d["summary"].strip()
            and isinstance(d.get("keywords"), list)
            and all(isinstance(k, str) for k in d["keywords"]))


def write_summary(it, data: dict, prov: dict) -> bool:
    """Write summary.json in a stable layout; False if nothing changed."""
    d = {"topic": data["topic"].strip(), "summary": data["summary"].strip(),
         "keywords": [k.strip() for k in data["keywords"] if k.strip()], "provenance": prov}
    new = json.dumps(d, indent=2, ensure_ascii=False) + "\n"
    if it.summary_path.is_file() and it.summary_path.read_text("utf-8") == new:
        return False
    atomic_write(it.summary_path, new)
    return True


def _prompt(it, text):
    note = OCR_NOTE if it.meta.get("files", {}).get("text_source", "").startswith("ocr") else ""
    return INSTRUCTION.format(text=text) + note


# ---- route 1: the Anthropic API (Message Batches)

def state_path(lib):
    return lib.index_dir / "summarize_batch.json"


def _params(it, text, model):
    return {"model": model, "max_tokens": SUMMARY_MAX_TOKENS, "system": SYSTEM,
            "messages": [{"role": "user", "content": _prompt(it, text)}],
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "low"}}


def _parse_message(msg) -> tuple[dict | None, str | None]:
    if msg.stop_reason in ("max_tokens", "refusal"):
        return None, f"stopped: {msg.stop_reason}"
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"bad JSON: {e}"
    return (d, None) if valid(d) else (None, "JSON does not match the schema")


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
    text, trunc = text_for(first)
    msg = client.messages.create(**_params(first, text, model))
    d, err = _parse_message(msg)
    if err:
        raise RuntimeError(f"{first.key}: {err}")
    if write_summary(first, d, provenance(first, model=model, via="api", text=text, truncated=trunc)):
        written.append(first.key)
    log(f"{first.key}: summarized (test request)")
    if not rest:
        return written
    reqs, ids = [], {}
    for i, it in enumerate(rest):
        text, trunc = text_for(it)
        cid = f"p{i:05d}"
        ids[cid] = {"key": it.key, "sha": sha256_file(it.text_path), "chars": len(text),
                    "truncated": trunc}
        reqs.append({"custom_id": cid, "params": _params(it, text, model)})
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


def claude_code_summarize(lib, items, model=CLAUDE_CODE_SUMMARY_MODEL, log=print) -> list[str]:
    outbound(lib, items)
    written = []
    for n, it in enumerate(items, 1):
        outbound(lib, [it])          # sharing may change during a long run
        text, trunc = text_for(it)
        try:
            env = run_claude(claude_code_args(model, SCHEMA),
                             "Summarize the paper whose text follows.\n\n" + _prompt(it, text))
        except Stop as e:
            log(f"stopping: {e}")
            break
        except RuntimeError as e:
            log(f"[{n}/{len(items)}] {it.key}: failed ({e}); left for the next run")
            continue
        d = env.get("structured_output")
        if not valid(d):
            log(f"[{n}/{len(items)}] {it.key}: output did not match the schema; left for the next run")
            continue
        real = next(iter(env.get("modelUsage") or {}), model)
        if write_summary(it, d, provenance(it, model=real, via="claude-code", text=text, truncated=trunc)):
            written.append(it.key)
        log(f"[{n}/{len(items)}] {it.key}: summarized")
    return written
