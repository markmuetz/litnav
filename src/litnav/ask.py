"""Ask: answer a question about the library with Claude and the read-only
literature tools, streaming events to the web page.

Two back ends (library.toml [ask] backend):
- "api": the Anthropic API (credentials from ANTHROPIC_API_KEY or an
  `ant auth login` profile), a streaming tool-use loop over litnav.tools.
- "claude-code": the local `claude` CLI in headless mode with only the litnav
  MCP server: built-in tools disabled, strict MCP config, run from a
  temporary directory outside the library, so it cannot read files directly.

Both use purpose `send_out`: confidential items are invisible. Events are
dicts: {"type": "status"|"text"|"tool"|"tool_result"|"done"|"error", ...}.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading

from . import sharing as S
from .tools import Tools

SYSTEM = """\
You are a research librarian for one researcher's personal literature library,
working through tools over it. Answer the question from the library, not from
memory: search, read the passages that matter, follow citations if useful, then
answer.

Rules:
- Cite every claim from the literature as [@key] and give the chunk id of the
  passage it rests on in parentheses, e.g. [@smith2021cold] (smith2021cold#3-1#a1b2c3d4).
  Quote short verbatim phrases when the exact wording matters.
- Search results are candidates, not answers: embeddings match topic, not claims,
  so check direction and negation in the passage itself before relying on it.
- Put exact names, acronyms and case studies in double quotes in searches.
- If the library does not answer the question, say so plainly and say what you
  searched for. Do not fill gaps from general knowledge without labelling them
  as such.
- Be concise: a direct answer first, then the evidence. Use Markdown."""

MAX_TURNS = 14


def _schema(props: dict, required=()) -> dict:
    return {"type": "object", "properties": props, "required": list(required),
            "additionalProperties": False}


S_STR, S_INT, S_BOOL = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}

TOOL_DEFS = [
    ("search_literature", "Search passages (default) or papers (papers=true). Hybrid keyword + "
     "semantic; \"quoted phrases\" are required. Returns chunk ids, citations, section titles and "
     "verbatim text.",
     _schema({"query": S_STR, "k": S_INT, "papers": S_BOOL, "mode": {"type": "string", "enum":
              ["hybrid", "keyword", "semantic"]}, "year_from": S_INT, "year_to": S_INT,
              "tag": S_STR, "author": S_STR}, ["query"])),
    ("get_chunk", "Fetch one passage by chunk id.", _schema({"chunk_id": S_STR}, ["chunk_id"])),
    ("list_sections", "List a paper's detected sections.", _schema({"key": S_STR}, ["key"])),
    ("get_section", "Read one section of a paper by slug (paginated with offset).",
     _schema({"key": S_STR, "section": S_STR, "offset": S_INT}, ["key", "section"])),
    ("get_paper", "Metadata, summary and abstract of one paper.", _schema({"key": S_STR}, ["key"])),
    ("related_papers", "Citation neighbours of a paper: direction cites, cited_by, similar or both.",
     _schema({"key": S_STR, "direction": {"type": "string", "enum": ["cites", "cited_by", "similar", "both"]},
              "k": S_INT}, ["key"])),
    ("list_papers", "Find papers by author, year range, title words or tag.",
     _schema({"author": S_STR, "year_from": S_INT, "year_to": S_INT, "title_words": S_STR,
              "tag": S_STR, "k": S_INT})),
    ("get_note", "The researcher's own notes on a paper.", _schema({"key": S_STR}, ["key"])),
]

_TYPES = {"string": str, "integer": int, "boolean": bool}


def _valid(args, schema) -> str | None:
    """A small validator: the SDK's tolerant parser can hand back partial input."""
    if not isinstance(args, dict):
        return "input is not an object"
    props = schema["properties"]
    for r in schema["required"]:
        if r not in args:
            return f"missing {r}"
    for k, v in args.items():
        if k not in props:
            return f"unknown argument {k}"
        t = _TYPES[props[k]["type"]]
        if not isinstance(v, t) or (t is int and isinstance(v, bool)):
            return f"{k} must be {props[k]['type']}"
        if "enum" in props[k] and v not in props[k]["enum"]:
            return f"{k} must be one of {props[k]['enum']}"
    return None


def _run_tool(tools: Tools, name: str, args: dict) -> tuple[str, bool]:
    try:
        out = getattr(tools, name)(**args)
        return json.dumps(out, default=str)[:60000], False
    except (ValueError, PermissionError, KeyError, TypeError) as e:
        return f"Error: {e}", True


def _summ(name, result: str) -> str:
    try:
        d = json.loads(result)
    except json.JSONDecodeError:
        return result[:200]
    if isinstance(d, list):
        keys = list(dict.fromkeys(x.get("key", "") for x in d if isinstance(x, dict)))
        return f"{len(d)} results: " + ", ".join(keys[:8])
    if isinstance(d, dict) and "key" in d:
        return f"{d['key']}" + (f" § {d.get('title', '')}" if d.get("title") else "")
    return result[:200]


def backend_status(lib) -> dict:
    cfg = lib.config.get("ask", {})
    be = cfg.get("backend", "claude-code")
    if be == "api":
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return {"backend": be, "ok": False, "reason": "install litnav[ask] (the anthropic SDK)"}
        has = any(os.environ.get(v) for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")) or \
            os.path.isdir(os.path.expanduser("~/.config/anthropic"))
        return {"backend": be, "ok": has, "model": cfg.get("model"),
                "reason": "" if has else "set ANTHROPIC_API_KEY (or run `ant auth login`) before `litnav serve`"}
    ok = bool(shutil.which("claude"))
    return {"backend": be, "ok": ok, "reason": "" if ok else "the `claude` CLI is not on PATH"}


def stream_answer(lib, question: str, history=None):
    st = backend_status(lib)
    if not st["ok"]:
        yield {"type": "error", "message": f"Ask is not available: {st['reason']}"}
        return
    try:
        if st["backend"] == "api":
            yield from _api(lib, question, history or [])
        else:
            yield from _claude_code(lib, question, history or [])
    except Exception as e:  # surface any failure to the page, not a dead stream
        yield {"type": "error", "message": f"{type(e).__name__}: {e}"}


def _api(lib, question, history):
    import anthropic
    cfg = lib.config.get("ask", {})
    model = cfg.get("model", "claude-opus-5-5")
    tools = Tools(lib, purpose=S.SEND_OUT)
    schemas = {n: s for n, _, s in TOOL_DEFS}
    tool_defs = [{"name": n, "description": d, "input_schema": s, "eager_input_streaming": True}
                 for n, d, s in TOOL_DEFS]
    client = anthropic.Anthropic()
    messages = [{"role": m["role"], "content": m["content"]} for m in history
                if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
    messages.append({"role": "user", "content": question})
    yield {"type": "status", "message": f"asking {model} ..."}
    json_retries = 0
    for _turn in range(MAX_TURNS):
        try:
            with client.beta.messages.stream(
                model=model,
                max_tokens=64000,
                system=SYSTEM,
                tools=tool_defs,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": cfg.get("effort", "medium")},
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                for event in stream:
                    if event.type == "text":
                        yield {"type": "text", "text": event.text}
                response = stream.get_final_message()
            json_retries = 0
        except ValueError:
            json_retries += 1          # unparseable streamed tool input: re-issue the turn
            if json_retries > 2:
                raise
            continue
        except anthropic.AuthenticationError:
            yield {"type": "error", "message": "Anthropic API: authentication failed (check ANTHROPIC_API_KEY)"}
            return
        except anthropic.RateLimitError:
            yield {"type": "error", "message": "Anthropic API: rate limited; try again shortly"}
            return
        except anthropic.APIStatusError as e:
            yield {"type": "error", "message": f"Anthropic API error {e.status_code}: {e.message}"}
            return
        except anthropic.APIConnectionError:
            yield {"type": "error", "message": "could not reach the Anthropic API"}
            return

        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason == "refusal":
            yield {"type": "error", "message": "the model declined to answer this question"}
            return
        if not uses:
            break
        if response.stop_reason == "max_tokens":
            yield {"type": "error", "message": "the answer was cut off (max_tokens)"}
            return
        results = []
        for b in uses:
            err = _valid(b.input, schemas.get(b.name, _schema({})))
            yield {"type": "tool", "name": b.name, "input": b.input if isinstance(b.input, dict) else {}}
            if b.name not in schemas or err:
                out, is_err = f"Error: {err or 'unknown tool'}", True
            else:
                out, is_err = _run_tool(tools, b.name, b.input)
            yield {"type": "tool_result", "name": b.name, "summary": _summ(b.name, out), "error": is_err}
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": out,
                            **({"is_error": True} if is_err else {})})
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": results})
        yield {"type": "text", "text": "\n\n"}
    else:
        yield {"type": "status", "message": "stopped after the maximum number of tool rounds"}
    yield {"type": "done"}


def _claude_code(lib, question, history):
    mcp = {"mcpServers": {"litnav": {"command": sys.executable,
                                     "args": ["-m", "litnav.cli", "--library", str(lib.root), "mcp"]}}}
    prompt = question
    if history:
        prev = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in history[-6:]
                           if isinstance(m.get("content"), str))
        prompt = f"Earlier in this conversation:\n{prev}\n\nNew question: {question}"
    with tempfile.TemporaryDirectory(prefix="litnav-ask-") as td:
        cfgp = os.path.join(td, "mcp.json")
        with open(cfgp, "w") as fh:
            json.dump(mcp, fh)
        # the prompt goes on stdin: as an argument, a question starting with "-"
        # would be read as an option
        cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose",
               "--mcp-config", cfgp, "--strict-mcp-config", "--tools", "",
               # no transcript of library passages, none of the user's settings or memory
               "--no-session-persistence", "--setting-sources", "", "--disable-slash-commands",
               "--allowedTools", "mcp__litnav", "--append-system-prompt", SYSTEM]
        model = lib.config.get("ask", {}).get("claude_code_model")
        if model:
            cmd += ["--model", model]
        yield {"type": "status", "message": "asking Claude Code ..."}
        errf = open(os.path.join(td, "stderr.txt"), "w+")    # a file, so a full pipe can't block
        p = subprocess.Popen(cmd, cwd=td, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf,
                             text=True)
        p.stdin.write(prompt)
        p.stdin.close()
        try:
            yield from _cc_events(p)
        finally:
            # the page may close the stream mid-answer: don't leave claude running
            if p.poll() is None:
                p.kill()
            p.wait()
        if p.returncode and p.returncode > 0:
            errf.seek(0)
            err = errf.read()[-500:]
            yield {"type": "error", "message": f"claude exited {p.returncode}: {err}"}
        errf.close()
    yield {"type": "done"}


PING = 2.0    # seconds of silence before a ping, so a closed page is noticed


def _lines(p, ping=None):
    """claude's stdout lines, with None after each `ping` seconds of silence.
    A reader thread does the blocking reads, so the consumer gets control back
    while claude works quietly; the web app writes each ping to the stream,
    finds the page gone, and the generator's close kills claude."""
    ping = ping or PING
    q = queue.Queue()

    def pump():
        for line in p.stdout:
            q.put(line)
        q.put(_EOF)
    threading.Thread(target=pump, daemon=True).start()
    while True:
        try:
            line = q.get(timeout=ping)
        except queue.Empty:
            yield None
            continue
        if line is _EOF:
            return
        yield line


_EOF = object()


def _cc_events(p, ping=None):
    """Map claude's stream-json output to Ask events."""
    names = {}
    for line in _lines(p, ping):
        if line is None:
            yield {"type": "ping"}
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "text":
                    yield {"type": "text", "text": b["text"] + "\n\n"}
                elif b.get("type") == "tool_use":
                    n = b.get("name", "").replace("mcp__litnav__", "")
                    names[b.get("id")] = n
                    yield {"type": "tool", "name": n, "input": b.get("input", {})}
        elif t == "user":
            for b in ev.get("message", {}).get("content", []):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    txt = c if isinstance(c, str) else " ".join(
                        x.get("text", "") for x in (c or []) if isinstance(x, dict))
                    n = names.get(b.get("tool_use_id"), "")
                    yield {"type": "tool_result", "name": n, "summary": _summ(n, txt),
                           "error": bool(b.get("is_error"))}
        elif t == "result" and ev.get("is_error"):
            yield {"type": "error", "message": str(ev.get("result", "Claude Code failed"))[:500]}
