"""Ask the user's local model a question that needs papers an outside model
may not read.

The user talks to one agent (Claude Code, say). When a question needs the
text of licensed papers, that agent calls `litnav ask-local` (MCP:
`ask_local_model`), and the local model answers from the library; the agent
gets back the answer, in the local model's words, with [@key] citations.

Two engines:
  opencode  OpenCode (an open-source agent) driven by the local model, with
            litnav's tools in `--local-model` mode: it searches, reads the
            sections it needs and answers. Better for questions across papers.
  simple    one call: the best-matching passages (from the named papers, or
            the whole library) and the question. Faster and more predictable
            with a small model.

Safeguards:
  - Confidential papers never reach the local model (purpose local_model).
  - OpenCode runs with every outbound connection sent to a dead proxy, so only
    the model server on localhost is reachable (it documents no telemetry
    switch), with all its own tools denied except litnav's, and with the
    shell environment of the calling agent removed.
  - The answer is scrubbed: any run of 12 or more words copied from a paper
    whose text may not go out is cut, so the calling agent gets the local
    model's account, not the paper's words.
Every question and answer is logged in derived/ask-local.jsonl.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import batch_runner, localmodel
from . import sharing as S

SHINGLE = 8
MIN_RUN = 12
MAX_ANSWER_CHARS = 6000
REMOVED = "[verbatim passage removed]"

SYSTEM = (
    "You answer questions for a researcher from their own literature library. Use only the "
    "library's papers (the passages or tools you are given), not memory. Cite every claim as "
    "[@key]. Say clearly when the library does not settle a question. Answer in your own words: "
    "quote at most a short phrase, never sentences. Be concise: a few short paragraphs at most.")

_WORD = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?")


# ---- scrubbing

def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def _shingles(words: list[str]) -> set[int]:
    return {hash(tuple(words[i:i + SHINGLE])) for i in range(len(words) - SHINGLE + 1)}


def scrub(answer: str, texts: list[str]) -> tuple[str, int]:
    """Cut runs of >= MIN_RUN words that appear verbatim in any of `texts`."""
    if not texts:
        return answer, 0
    known: set[int] = set()
    for t in texts:
        known |= _shingles(_words(t))
    spans = [(m.start(), m.end(), m.group(0).lower()) for m in _WORD.finditer(answer)]
    ws = [w for _, _, w in spans]
    covered = [False] * len(ws)
    for i in range(len(ws) - SHINGLE + 1):
        if hash(tuple(ws[i:i + SHINGLE])) in known:
            for j in range(i, i + SHINGLE):
                covered[j] = True
    runs, i = [], 0
    while i < len(ws):
        if covered[i]:
            j = i
            while j < len(ws) and covered[j]:
                j += 1
            if j - i >= MIN_RUN:
                runs.append((spans[i][0], spans[j - 1][1]))
            i = j
        else:
            i += 1
    out = answer
    for a, b in reversed(runs):
        out = out[:a] + REMOVED + out[b:]
    return out, len(runs)


def _withheld_texts(lib, keys) -> list[str]:
    """Texts of the papers among `keys` whose text may not go out."""
    out = []
    for k in dict.fromkeys(keys):
        kk = lib.resolve(k)
        if not kk:
            continue
        it = lib.get(kk)
        if it.has_text and not it.allowed(S.SEND_OUT):
            out.append(it.text())
    return out


CITE = re.compile(r"(?:\[|;\s*)@([A-Za-z][\w.:-]*?)[.:-]?(?=[\]\s;,§])")    # [@key], [@key § 2], [@a; @b]


# ---- engines

def _simple(lib, question: str, keys: list[str] | None) -> tuple[str, list[str], str]:
    from . import index, search
    url, model, kw = localmodel.endpoint(lib)
    con = index.connect(lib, readonly=True)
    try:
        try:
            from .commands.searching import embedder_for
            emb = embedder_for(lib)
        except Exception:
            emb = None
        f = search.Filters(keys=[lib.get(k).key for k in keys]) if keys else None
        hits = search.search(con, question, embedder=emb, k=12, filters=f, purpose=S.LOCAL_MODEL)
    finally:
        con.close()
    if keys:
        for k in keys:
            localmodel.gate(lib.get(k))
    budget, parts, used = localmodel.MAX_VERIFY_CHARS * 2, [], []
    for h in hits:
        if budget <= 0:
            break
        t = h["text"][:budget]
        budget -= len(t)
        parts.append(f"[@{h['key']}] {h['title']} ({h['year']}), § {h.get('section_title') or h['kind']}\n{t}")
        used.append(h["key"])
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Question: {question}\n\nPassages from the library:\n\n"
             + "\n\n".join(parts) + "\n\nAnswer the question from these passages, citing [@key]."}]
    text, real = batch_runner.chat(url, model, msgs, **kw)
    return batch_runner._THINK.sub("", text).strip(), used, real


def opencode_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "litnav" / "opencode"


PROVIDER = "litnav"


def opencode_config(lib, cfg: dict) -> dict:
    """OpenCode's config for the local librarian: the endpoint of the chosen
    profile (any OpenAI-compatible server: Ollama, vLLM through a tunnel),
    litnav's tools in local mode, and nothing else (no shell, edits, web,
    subagents)."""
    model = cfg["model"]
    deny_all = [{"action": "*", "resource": "*", "effect": "deny"},
                {"action": "litnav_*", "resource": "*", "effect": "allow"}]
    return {
        "$schema": "https://opencode.ai/config.json",
        "update": "disable",
        "share": "disabled",
        "model": f"{PROVIDER}/{model}",
        "providers": {PROVIDER: {
            "name": f"litnav local model ({cfg.get('profile', 'default')})",
            "package": "@opencode/ai/providers/openai-compatible",
            "settings": {"baseURL": cfg["url"], "apiKey": "{env:LITNAV_MODEL_API_KEY}"},
            "models": {model: {"name": model, "capabilities": {"tools": True},
                               "limit": {"context": int(cfg.get("context", 32768)),
                                         "output": 8192}}}}},
        "default_agent": "librarian",
        "mcp": {"servers": {"litnav": {
            "type": "local",
            "command": [sys.executable, "-m", "litnav.cli", "--library", str(lib.root), "mcp",
                        "--local-model"],
            # LITNAV_ACCESS_LOG comes from the inherited environment
            "environment": {"LITNAV_LOCAL_AGENT": "1"},
            "codemode": False}}},
        "permissions": deny_all,
        "agents": {"librarian": {
            "description": "Answers from the user's literature library with litnav's tools",
            "mode": "primary", "system": SYSTEM + " Use the litnav tools: search_literature, then "
            "list_sections and get_section for the papers that matter.",
            "permissions": deny_all}},
    }


def isolated_env(access_log: Path, api_key: str = "", cwd: Path | None = None) -> dict:
    """The calling agent's environment, minus its markers, with every outbound
    connection except to localhost sent to a closed port."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE") or k.startswith("ANTHROPIC"))}
    dead = "http://127.0.0.1:9"
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env[k] = dead
    env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
    env["LITNAV_ACCESS_LOG"] = str(access_log)
    env["LITNAV_LOCAL_AGENT"] = "1"
    env["LITNAV_MODEL_API_KEY"] = api_key or "none"
    if cwd:
        # OpenCode finds its project config from $PWD, not the real working
        # directory: without this it would read the caller's directory instead
        env["PWD"] = str(cwd)
    return env


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_HEADER = re.compile(r"^\s*>\s*\w+\s*·.*$", re.M)          # "> librarian · model"


def clean_output(text: str) -> str:
    return _HEADER.sub("", _ANSI.sub("", text)).strip()


def _opencode(lib, question: str, keys: list[str] | None, timeout: float) -> tuple[str, list[str], str]:
    exe = shutil.which("opencode")
    if not exe:
        raise ValueError("OpenCode is not installed (`brew install opencode`); or use --engine simple")
    localmodel.endpoint(lib)                     # fails early if nothing is set up
    cfg = lib.local_model
    model = cfg["model"]
    d = opencode_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "opencode.json").write_text(json.dumps(opencode_config(lib, cfg), indent=2), encoding="utf-8")
    prompt = question
    if keys:
        prompt += "\n\nPapers to use: " + ", ".join(f"[@{lib.get(k).key}]" for k in keys)
        for k in keys:
            localmodel.gate(lib.get(k))
    with tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False) as f:
        log = Path(f.name)
    try:
        p = subprocess.run([exe, "run", "--standalone", "--agent", "librarian",
                            "--model", f"{PROVIDER}/{model}", "--archived", "--title", "litnav ask-local",
                            prompt], cwd=d, env=isolated_env(log, localmodel.api_key(cfg), d),
                           capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        accessed = [l.strip() for l in log.read_text("utf-8").splitlines() if l.strip()]
    finally:
        log.unlink(missing_ok=True)
    if p.returncode != 0:
        raise RuntimeError(f"opencode failed ({p.returncode}): {(p.stderr or p.stdout).strip()[-500:]}")
    return (batch_runner._THINK.sub("", clean_output(p.stdout)).strip(), accessed,
            f"{model} ({cfg.get('profile', 'default')}) via opencode")


# ---- the entry point

def log_path(lib) -> Path:
    return lib.root / "derived" / "ask-local.jsonl"


def ask(lib, question: str, *, keys: list[str] | None = None, engine: str | None = None,
        timeout: float = 900) -> dict:
    engine = engine or lib.config.get("local_model", {}).get("engine") or \
        ("opencode" if shutil.which("opencode") else "simple")
    if engine == "opencode":
        answer, accessed, model = _opencode(lib, question, keys, timeout)
    elif engine == "simple":
        answer, accessed, model = _simple(lib, question, keys)
    else:
        raise ValueError(f"unknown engine {engine!r}: opencode or simple")
    cited = list(dict.fromkeys(CITE.findall(answer)))
    clean, removed = scrub(answer, _withheld_texts(lib, list(accessed) + cited + list(keys or [])))
    rec = {"date": dt.date.today().isoformat(), "question": question, "keys": keys or [],
           "engine": engine, "model": model, "answer": clean[:MAX_ANSWER_CHARS], "cited": cited,
           "read": list(dict.fromkeys(accessed)), "verbatim_removed": removed}
    p = log_path(lib)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec
