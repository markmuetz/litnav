"""Work done by a model the owner runs: summaries and claim checks.

A library whose owner may not send licensed papers to an outside model
(`licensed_to_models = false`) still needs summaries, and an agent still
needs to check a claim against a paper it may not read. Both go to a local
model instead, through the `local_model` purpose: this machine's Ollama /
llama.cpp / MLX server, or a bigger model run now and then as a batch on the
owner's HPC account (JASMIN/ORCHID). Confidential items never go.

What comes back is model-written (`send_derived`): a summary, or a verdict
with its location and confidence. The paper's own words do not come back to
an agent, except a short quotation from a paper that may be sent out anyway,
or when the owner turns on `[verify] quotes`.

Jobs are the unit for both ways of running:
  now     `run_now()`: each job straight to [local_model] url
  batch   `queue add` -> `export` a self-contained folder (jobs.jsonl, the
          stdlib-only runner, a SLURM script) -> run it on the cluster ->
          `import` the results. A result whose paper text changed since the
          job was made is dropped, not applied.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
from pathlib import Path

from . import batch_runner
from . import sharing as S
from .net import Refused
from .util import atomic_write, sha256_file

VERIFY_PROMPT_VERSION = 2     # 2: passages are ids from an enum, not free text
MAX_VERIFY_CHARS = 14000
QUOTE_MAX_WORDS = 25

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["supported", "partly_supported", "contradicted",
                                               "not_found"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "passages": {"type": "array", "maxItems": 3, "items": {"type": "string"},
                     "description": "ids of the passages the verdict rests on (none if not_found)"},
        "explanation": {"type": "string",
                        "description": "One or two sentences in your own words: what the paper says "
                                       "about the claim. Do not copy long stretches of the text."},
        "quote": {"type": "string", "description": f"At most {QUOTE_MAX_WORDS} words copied exactly "
                                                   "from the passage that decides it, or ''"},
    },
    "required": ["verdict", "confidence", "passages", "explanation", "quote"],
    "additionalProperties": False,
}

def with_passage_ids(schema: dict, ids: list[str]) -> dict:
    """The schema with `passages` limited to these ids. The server constrains
    the reply to the schema, so the field can't carry paper text (or a
    misspelt id): the explanation is the only free text left."""
    s = json.loads(json.dumps(schema))
    s["properties"]["passages"]["items"]["enum"] = list(ids)
    return s


def cited_passages(out: dict, ids: list[str]) -> list[str]:
    """The valid passage ids a reply names (`passages`, or the old `passage` string)."""
    got = out.get("passages")
    if got is None:
        got = re.split(r"[,;\s]+", out.get("passage") or "")
    if isinstance(got, str):
        got = [got]
    return [g for g in dict.fromkeys(x.strip() for x in got if isinstance(x, str)) if g in ids]


VERIFY_SYSTEM = (
    "You check claims against one academic paper for a researcher. You see the claim and "
    "numbered passages extracted from the paper's PDF (noisy: running heads, OCR errors). "
    "Decide only from the passages. 'supported': the passages state it. 'partly_supported': "
    "some of it is stated, some is not or differs. 'contradicted': the passages say otherwise. "
    "'not_found': the passages do not settle it. Bibliographic claims (volume, pages, article "
    "number, DOI, year, authors) are decided from the header or citation line. In `passages` "
    "give the ids of the passages the verdict rests on. Reply with JSON only.")


def _id(*parts) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:12]


def gate(it):
    if not it.allowed(S.LOCAL_MODEL):
        raise Refused(f"{it.key}: not sent to any model ({it.sharing})")


def endpoint(lib) -> tuple[str, str, dict]:
    cfg = lib.local_model
    url, model = cfg.get("url"), cfg.get("model")
    if not url or not model:
        raise ValueError("no local model set up: add [local_model] url and model to library.toml "
                         "(e.g. url = \"http://localhost:11434/v1\", model = \"qwen3:14b\" for Ollama), "
                         "or queue the work for a batch run: `litnav queue add ...`")
    kw = {"timeout": float(cfg.get("timeout", 600))}
    key = api_key(cfg)
    if key:
        kw["api_key"] = key
    return url, model, kw


def api_key(cfg: dict) -> str:
    """The endpoint's key: from the env var named by api_key_env, or the file
    api_key_file (e.g. a vLLM token, mode 0600). Never in library.toml."""
    import os
    if cfg.get("api_key_env"):
        return os.environ.get(cfg["api_key_env"], "")
    if cfg.get("api_key_file"):
        f = Path(os.path.expandvars(cfg["api_key_file"])).expanduser()
        if not f.is_file():
            raise ValueError(f"api_key_file {f} not found (profile {cfg.get('profile')})")
        return f.read_text("utf-8").strip()
    return ""


def ping(cfg: dict, timeout: float = 5) -> str:
    """'up (models: ...)' or why not, for `litnav models`."""
    import urllib.request
    if not cfg.get("url"):
        return "not set up"
    try:
        key = api_key(cfg)
        req = urllib.request.Request(cfg["url"].rstrip("/") + "/models",
                                     headers={"Authorization": f"Bearer {key}"} if key else {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ids = [m.get("id") for m in json.load(r).get("data", [])]
    except Exception as e:                       # noqa: BLE001 - report, don't fail
        return f"down ({type(e).__name__}: {str(e)[:80]})"
    have = cfg.get("model") in ids
    return f"up, serves {', '.join(map(str, ids[:4])) or '-'}" + ("" if have else
                                                                  f" (not {cfg.get('model')}!)")


def model_info(lib, timeout: float = 10) -> dict:
    """What the server says about the model (for eval and summary metadata):
    Ollama's family, size and quantization, or vLLM's context and weights."""
    import socket
    import urllib.parse
    import urllib.request
    url, model, kw = endpoint(lib)
    info = {"model": model, "profile": lib.local_model.get("profile") or "default"}
    host = urllib.parse.urlparse(url).hostname or ""
    info["machine"] = socket.gethostname().split(".")[0] if host in ("localhost", "127.0.0.1") else host
    hdr = {"Authorization": f"Bearer {kw['api_key']}"} if kw.get("api_key") else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url.rstrip("/") + "/models", headers=hdr),
                                    timeout=timeout) as r:
            for m in json.load(r).get("data", []):
                if m.get("id") == model:
                    info.update({k: m[k] for k in ("max_model_len", "root", "owned_by") if k in m})
    except Exception as e:                       # noqa: BLE001
        info["models_error"] = f"{type(e).__name__}"
    base = url.rstrip("/")
    base = base[:-3] if base.endswith("/v1") else base
    try:
        req = urllib.request.Request(base + "/api/show", data=json.dumps({"model": model}).encode(),
                                     headers={"Content-Type": "application/json", **hdr}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        info["server"] = "ollama"
        info.update({k: v for k, v in (d.get("details") or {}).items()
                     if k in ("family", "parameter_size", "quantization_level", "format")})
        info["digest"] = (d.get("digest") or "")[:12] or None
    except Exception:                            # noqa: BLE001 - not Ollama
        info.setdefault("server", "openai-compatible")
    return {k: v for k, v in info.items() if v is not None}


# ---- jobs

# A local model's context is far smaller than Claude's: Qwen3 has 32k tokens,
# and Ollama gives a model only a few thousand unless OLLAMA_CONTEXT_LENGTH is
# set, cutting the rest off silently. ~4 characters a token; leave room for the
# prompt and the reply. Override with [local_model] max_chars.
LOCAL_MAX_CHARS = 90_000


def max_chars(lib) -> int:
    return int(lib.local_model.get("max_chars", LOCAL_MAX_CHARS))


def summarize_job(it, limit: int | None = None) -> dict:
    from . import derived
    gate(it)
    inp = derived.summary_input(it, limit or max_chars(it.lib))
    sha = sha256_file(it.text_path)
    return {"id": f"summarize-{it.key}-{sha[:12]}-v{derived.PROMPT_VERSION}", "kind": "summarize",
            "key": it.key, "text_sha256": sha, "chars_sent": inp["chars"], "truncated": inp["truncated"],
            "schema": inp["schema"], "passages": inp["ids"],
            "messages": [{"role": "system", "content": derived.SYSTEM},
                         {"role": "user", "content": inp["prompt"] + "\n\nReply with JSON only."}]}


def rephrase_job(job: dict, out: dict, err: str) -> dict:
    """The same summary job, asked once more after a refusal for copying: the
    model sees its reply and the copied words."""
    from . import derived
    return {**job, "id": f"{job['id']}-r", "rephrased": True,
            "messages": job["messages"] + [{"role": "assistant", "content": json.dumps(out, ensure_ascii=False)},
                                           {"role": "user", "content": derived.rephrase_note(err)}]}


def item_check_jobs(lib, it, *, force: bool = False) -> list[dict]:
    """A verify job for each key item of a paper's summary not yet checked
    (at the current verify prompt), with the passages the item names added
    to the ones search finds for it."""
    from . import derived
    s = it.summary() or {}
    out = []
    for n, x in enumerate(s.get("key_items") or []):
        c = x.get("check") or {}
        if not force and c.get("prompt_version") == VERIFY_PROMPT_VERSION and c.get("statement") == x["statement"]:
            continue
        j = verify_job(lib, it.key, x["statement"], extra=x.get("chunks") or [])
        j["summary_item"] = {"n": n, "statement": x["statement"],
                             "summary_prompt": s.get("provenance", {}).get("prompt_version", derived.PROMPT_VERSION)}
        j["id"] = f"{j['id']}-item{n}"
        out.append(j)
    return out


def _record_item_check(it, job, rec) -> bool:
    """Put a verdict into the summary's key item, if the summary still has it."""
    from . import derived
    s = it.summary()
    si = job["summary_item"]
    items = (s or {}).get("key_items") or []
    if not (0 <= si["n"] < len(items)) or items[si["n"]].get("statement") != si["statement"]:
        return False
    items[si["n"]]["check"] = {"verdict": rec["verdict"], "confidence": rec["confidence"],
                               "passages": rec["location"].get("passages", []), "model": rec["model"],
                               "via": rec["via"], "prompt_version": rec["prompt_version"],
                               "statement": si["statement"], "date": rec["date"]}
    atomic_write(it.summary_path, json.dumps(s, indent=2, ensure_ascii=False) + "\n")
    return True


def _page(text: str, offset: int) -> int | None:
    return text.count("\f", 0, offset) + 1 if "\f" in text else None


def passages_for(lib, it, claim: str, k: int = 8, extra: list[str] | None = None) -> list[dict]:
    """The paper's opening passages (header, citation line, abstract), any
    `extra` passages named (a summary item's own), and the ones that best
    match the claim, in reading order, within a budget."""
    from . import index, search
    con = index.connect(lib, readonly=True)
    try:
        first = [dict(r) for r in con.execute(
            "SELECT id, section_title, kind, start, \"end\", text FROM chunks WHERE key=? "
            "ORDER BY ord LIMIT 2", (it.key,))]
        try:
            from .commands.searching import embedder_for
            emb = embedder_for(lib)
        except Exception:
            emb = None
        f = search.Filters(keys=[it.key])
        hits = search.search(con, claim, embedder=emb, k=k, filters=f, purpose=S.LOCAL_MODEL)
        named = [dict(r) for r in con.execute(
            f"SELECT id, section_title, kind, start, \"end\", text FROM chunks WHERE key=? AND id IN "
            f"({','.join('?' * len(extra or []))})", (it.key, *(extra or [])))] if extra else []
        ordmap = {r["id"]: r["ord"] for r in con.execute(
            "SELECT id, ord FROM chunks WHERE key=?", (it.key,))}
    finally:
        con.close()
    seen, out, budget = set(), [], MAX_VERIFY_CHARS
    for r in first + named + hits:
        if r["id"] in seen or budget <= 0:
            continue
        seen.add(r["id"])
        t = r["text"][:budget]
        budget -= len(t)
        out.append({"id": r["id"], "section": r.get("section_title") or r.get("kind") or "",
                    "start": r["start"], "text": t})
    out.sort(key=lambda p: ordmap.get(p["id"], 0))
    return out


def verify_job(lib, key: str, claim: str, extra: list[str] | None = None) -> dict:
    it = lib.get(key)
    gate(it)
    if not it.has_text:
        raise ValueError(f"{it.key}: no text to check against (`litnav extract {it.key}`)")
    ps = passages_for(lib, it, claim, extra=extra)
    body = "\n\n".join(f"[{p['id']}] ({p['section']})\n{p['text']}" for p in ps)
    sha = sha256_file(it.text_path)
    return {"id": f"verify-{it.key}-{_id(claim, sha)}", "kind": "verify", "key": it.key,
            "claim": claim, "text_sha256": sha, "passages": [p["id"] for p in ps],
            "schema": with_passage_ids(VERIFY_SCHEMA, [p["id"] for p in ps]),
            "messages": [{"role": "system", "content": VERIFY_SYSTEM},
                         {"role": "user", "content": f"Claim: {claim}\n\nPaper: {it.title} "
                          f"({it.year})\n\nPassages:\n{body}\n\nReply with JSON only."}]}


# ---- results

def verify_log(lib) -> Path:
    return lib.root / "derived" / "verify.jsonl"


def _valid_verdict(d) -> bool:
    return (isinstance(d, dict) and d.get("verdict") in VERIFY_SCHEMA["properties"]["verdict"]["enum"]
            and d.get("confidence") in ("high", "medium", "low"))


def apply_result(lib, job: dict, res: dict, *, via: str) -> dict:
    """Write one finished job into the library. Returns a status record."""
    it = lib.get(job["key"])
    if not res.get("ok"):
        return {"id": job["id"], "status": "failed", "error": res.get("error", "")}
    if not it.has_text or sha256_file(it.text_path) != job["text_sha256"]:
        return {"id": job["id"], "status": "stale", "error": "the paper's text changed since"}
    out = res.get("output")
    if job["kind"] == "summarize":
        from . import derived
        err = derived.check(it, out)
        if err:
            st = {"id": job["id"], "status": "failed", "error": err}
            if err.startswith("copies") and not job.get("rephrased"):
                st["rephrase"] = rephrase_job(job, out, err)
            return st
        prov = {"model": res.get("model", ""), "via": via, "prompt_version": derived.PROMPT_VERSION,
                "text_sha256": job["text_sha256"],
                "text_source": it.meta.get("files", {}).get("text_source", ""),
                "chars_sent": job["chars_sent"], "truncated": job["truncated"]}
        derived.write_summary(it, out, prov)
        return {"id": job["id"], "status": "ok", "key": it.key}
    if job["kind"] == "verify":
        if not _valid_verdict(out):
            return {"id": job["id"], "status": "failed", "error": "verdict did not match the schema"}
        rec = verdict_record(lib, it, job, out, model=res.get("model", ""), via=via)
        log = verify_log(lib)
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if job.get("summary_item") and not _record_item_check(it, job, rec):
            return {"id": job["id"], "status": "stale", "error": "the summary changed since"}
        return {"id": job["id"], "status": "ok", "key": it.key, "record": rec}
    return {"id": job["id"], "status": "failed", "error": f"unknown kind {job['kind']!r}"}


def verdict_record(lib, it, job, out, *, model, via) -> dict:
    cited = cited_passages(out, job["passages"])
    pid = cited[0] if cited else ""
    loc = {"passages": cited} if cited else {}
    if pid:
        from . import index
        con = index.connect(lib, readonly=True)
        try:
            r = con.execute("SELECT section_title, kind, start FROM chunks WHERE id=?", (pid,)).fetchone()
        finally:
            con.close()
        if r:
            loc |= {"passage": pid, "section": r["section_title"] or r["kind"],
                   "page": _page(it.text(), r["start"])}
    quote = " ".join((out.get("quote") or "").split())
    if quote and quote.lower()[:60] not in " ".join(it.text().split()).lower():
        quote, loc["quote_unverified"] = "", True     # not verbatim: drop it rather than mislead
    return {"date": dt.date.today().isoformat(), "id": job["id"], "key": it.key,
            "claim": job["claim"], "verdict": out["verdict"], "confidence": out["confidence"],
            "location": loc, "explanation": (out.get("explanation") or "").strip()[:600],
            "quote": quote, "model": model, "via": via, "prompt_version": VERIFY_PROMPT_VERSION}


def for_agent(lib, rec: dict) -> dict:
    """What an agent may see of a verdict: no quotation from a paper whose
    text may not be sent out, unless the owner allows short quotes."""
    it = lib.get(rec["key"])
    allow_quotes = bool(lib.config.get("verify", {}).get("quotes", False))
    r = dict(rec)
    if r.get("quote") and not it.allowed(S.SEND_OUT):
        if not (allow_quotes and len(r["quote"].split()) <= QUOTE_MAX_WORDS):
            r["quote"] = "(withheld: licensed text)"
    return r


# ---- run now

def run_now(lib, jobs: list[dict], *, log=print) -> list[dict]:
    url, model, kw = endpoint(lib)
    out = []
    for i, job in enumerate(jobs, 1):
        gate(lib.get(job["key"]))         # sharing may change during a long run
        res = batch_runner.run_job(job, url, model, **kw)
        st = apply_result(lib, job, res, via="local")
        if st.get("rephrase"):                     # copied from the paper: ask once more
            log(f"[{i}/{len(jobs)}] {job['key']} {job['kind']}: {st['error']}; asking again")
            job = st["rephrase"]
            st = apply_result(lib, job, batch_runner.run_job(job, url, model, **kw), via="local")
        out.append(st)
        log(f"[{i}/{len(jobs)}] {job['key']} {job['kind']}: {st['status']}"
            + (f" ({st['error']})" if st.get("error") else ""))
    return out


# ---- the batch queue

def queue_path(lib) -> Path:
    return lib.index_dir / "queue.jsonl"


def queued(lib) -> list[dict]:
    p = queue_path(lib)
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text("utf-8").splitlines() if l.strip()]


def enqueue(lib, jobs: list[dict]) -> int:
    have = {j["id"] for j in queued(lib)}
    new = [j for j in jobs if j["id"] not in have]
    p = queue_path(lib)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for j in new:
            f.write(json.dumps(j, ensure_ascii=False) + "\n")
    return len(new)


def _rewrite_queue(lib, jobs):
    atomic_write(queue_path(lib), "".join(json.dumps(j, ensure_ascii=False) + "\n" for j in jobs))


SLURM = """#!/bin/bash
# A litnav model batch on a GPU node. CHECK the partition, account, QOS and
# GPU request against the current JASMIN ORCHID documentation before use:
# these lines are a template, not tested on ORCHID.
#SBATCH --job-name=litnav-batch
#SBATCH --partition=orchid
#SBATCH --account=orchid
#SBATCH --gres=gpu:1
#SBATCH --time=04:00:00
#SBATCH --output=litnav-batch-%j.log

set -euo pipefail
cd "$(dirname "$0")"
MODEL=${MODEL:-@MODEL@}
PORT=${PORT:-8000}

# Serve the model (vLLM shown; any OpenAI-compatible server works).
vllm serve "$MODEL" --port "$PORT" --max-model-len 32768 &
SERVER=$!
trap 'kill $SERVER' EXIT
until curl -sf "http://localhost:$PORT/v1/models" >/dev/null; do sleep 10; done

# Stop starting jobs 15 min before the time limit; rerun the script to resume.
python3 run_jobs.py --url "http://localhost:$PORT/v1" --model "$MODEL" \\
    --limit-minutes 225 jobs.jsonl results.jsonl
"""

README = """litnav model batch: %(n)d job(s), exported %(date)s from %(lib)s

Contains the paper text the jobs need. Keep it on your own account (JASMIN
home or group workspace) and delete it once the results are back.

  jobs.jsonl      the jobs
  run_jobs.py     the runner (Python 3 standard library only)
  orchid.slurm    a SLURM template: check its #SBATCH lines first
  results.jsonl   written by the runner; a rerun resumes

Run:
  sbatch orchid.slurm                       # or, with a server already up:
  python3 run_jobs.py --url http://HOST:PORT/v1 --model MODEL jobs.jsonl results.jsonl
Back on the laptop:
  litnav queue import <this folder>
"""


def export(lib, out: Path, *, model: str = "") -> int:
    jobs = queued(lib)
    if not jobs:
        return 0
    for j in jobs:                          # re-check: sharing may have changed since queuing
        gate(lib.get(j["key"]))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "jobs.jsonl").exists():
        raise FileExistsError(f"{out}/jobs.jsonl exists: export into a new folder")
    atomic_write(out / "jobs.jsonl", "".join(json.dumps(j, ensure_ascii=False) + "\n" for j in jobs))
    shutil.copyfile(Path(batch_runner.__file__), out / "run_jobs.py")
    model = model or lib.local_model.get("batch_model") or "Qwen/Qwen3-32B"
    (out / "orchid.slurm").write_text(SLURM.replace("@MODEL@", model), encoding="utf-8")
    (out / "README.txt").write_text(README % {"n": len(jobs), "date": dt.date.today().isoformat(),
                                               "lib": lib.root}, encoding="utf-8")
    return len(jobs)


def import_results(lib, folder: Path, *, log=print) -> dict:
    folder = Path(folder)
    jobs = {j["id"]: j for j in (json.loads(l) for l in
                                 (folder / "jobs.jsonl").read_text("utf-8").splitlines() if l.strip())}
    counts = {"ok": 0, "failed": 0, "stale": 0, "unknown": 0}
    applied = set()
    for line in (folder / "results.jsonl").read_text("utf-8").splitlines():
        try:
            res = json.loads(line)
        except json.JSONDecodeError:
            continue
        job = jobs.get(res.get("id"))
        if not job:
            counts["unknown"] += 1
            continue
        if res["id"] in applied:
            continue
        st = apply_result(lib, job, res, via="batch")
        counts[st["status"]] += 1
        if st["status"] in ("ok", "stale"):
            applied.add(res["id"])
        if st.get("rephrase"):                      # the next batch asks again
            enqueue(lib, [st["rephrase"]])
            applied.add(res["id"])
        if st["status"] != "ok":
            log(f"  {job['key']} {job['kind']}: {st['status']} ({st.get('error', '')})")
    _rewrite_queue(lib, [j for j in queued(lib) if j["id"] not in applied])
    return counts
