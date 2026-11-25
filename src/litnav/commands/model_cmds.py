"""verify, queue: work for a model the owner runs (see litnav.localmodel)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .. import localmodel
from ..library import Library


def add(sub):
    p = sub.add_parser("verify", help="check a claim against a paper with your local model; "
                       "prints the verdict, where, and how sure (not the paper's text)")
    p.add_argument("key", nargs="?")
    p.add_argument("claim", nargs="?")
    p.add_argument("--queue", action="store_true", help="add to the batch instead of running now")
    p.add_argument("--log", action="store_true", help="show past verdicts (for KEY, if given)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("eval-model", help="evaluate the local model's claim checks on an eval set "
                       "(run: stores every reply; report: scores them)")
    p.add_argument("action", choices=["run", "report", "push"],
                   help="push: send it as a batch to the cluster spool ([batch_remote]); "
                        "`litnav queue pull` brings the replies back")
    p.add_argument("eval", help="eval set (JSONL), e.g. eval/model-claims.jsonl")
    p.add_argument("--run-dir", help="default derived/model-eval/<date>-<model>")
    p.add_argument("--limit-minutes", type=float, default=88)
    p.add_argument("--no-submit", action="store_true", help="push: don't submit the worker job")
    p.add_argument("--against", help="report: another run folder on the same set, compared claim by claim "
                   "(McNemar's test)")
    p.set_defaults(func=cmd_eval_model)

    p = sub.add_parser("paper-eval", help="evaluate models at whole-paper tasks (summary, extract) on open "
                       "papers, judged by Claude: pick, gold, run, push, baseline, judge, report")
    p.add_argument("action", choices=["pick", "gold", "run", "push", "baseline", "judge", "report", "compare",
                                      "agree", "reask", "rerun"],
                   help="compare: --run-dir A --against B, item by item; agree: Claude's and Gemini's judgments "
                        "of one run")
    p.add_argument("--eval", default="eval/paper-eval.jsonl")
    p.add_argument("--task", choices=["summary", "extract", "mentions"])
    p.add_argument("--run-dir", help="judge/report: the run folder (default: the newest for --task)")
    p.add_argument("-n", type=int, default=20, help="pick: how many papers")
    p.add_argument("--judge-model", help="gold/judge: the judge's model (default opus for Claude, "
                   "gemini-4-neon-high for Gemini)")
    p.add_argument("--judge", choices=["claude", "gemini"], default="claude",
                   help="judge/report: which judge (Gemini through the agy CLI; open papers only)")
    p.add_argument("--jobs", type=int, default=4, help="gold/judge: Claude calls at a time")
    p.add_argument("--no-submit", action="store_true")
    p.add_argument("--chars", type=int, help="run/push: characters of each paper the model reads "
                   "(default 72000, the pilot's; 0 = the whole paper); for mentions, the characters per call "
                   "(default 20000; the whole paper is read). Fixed per run, recorded in run.json")
    p.add_argument("--against", help="compare: the second run folder")
    p.add_argument("--key", "-k", action="append", dest="keys",
                   help="rerun: a paper to run again into --run-dir, all its calls (repeat); pushed to the spool "
                        "chosen by -M")
    p.add_argument("--push", action="store_true", help="reask: send the re-asks to the cluster spool "
                   "(after `queue pull`), not to [local_model]")
    p.set_defaults(func=cmd_paper_eval)

    p = sub.add_parser("g0", help="G0: induce the domain schema from the papers, blind (survey, inventories, "
                       "tally, then Fable drafts and Opus and Gemini review): sample, push, status, tally, "
                       "synthesize, review, revise")
    p.add_argument("action", choices=["sample", "jobs", "push", "status", "tally", "synthesize", "review", "revise"],
                   help="jobs: write jobs.jsonl without pushing (then `litnav claude-run`)")
    p.add_argument("--open-only", action="store_true", help="sample: the corpus is the open papers")
    p.add_argument("--like", help="sample: copy this run folder's sample and corpus")
    p.add_argument("--run-dir", help="the run folder (default: the newest under derived/g0)")
    p.add_argument("-n", type=int, default=100, help="sample: papers to inventory")
    p.add_argument("--clusters", type=int, default=12, help="sample: embedding clusters to stratify by")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--label", default="", help="sample: a label for the run folder")
    p.add_argument("--open", type=int, default=10, help="synthesize: open papers whose text Fable reads")
    p.add_argument("--open-chars", type=int, default=30_000, help="synthesize: characters of each")
    p.add_argument("--model", help="synthesize/revise: the drafting model (default Fable)")
    p.add_argument("--no-submit", action="store_true")
    p.set_defaults(func=cmd_g0)

    p = sub.add_parser("model-registry", help="which models each paper runs, at what grid spacing, and what it "
                       "states about deep convection; aggregated by model family (jobs, then claude-run or push, build)")
    p.add_argument("action", choices=["jobs", "build", "score", "classes"],
                   help="score: leave one paper out against stated cases, by bracketing and the sensitivity "
                        "checks; classes: induce the class vocabulary from the papers' definitions")
    p.add_argument("--run-dir")
    p.add_argument("--all", action="store_true", help="jobs: every paper the local model may read (default: open only)")
    p.add_argument("--label", default="")
    p.set_defaults(func=cmd_model_registry)

    p = sub.add_parser("topics", help="topics drawn from the literature: per-paper aspects (open vocabulary), "
                       "consensus clusters, named by a strong model (jobs, then claude-run; cluster; name)")
    p.add_argument("action", choices=["jobs", "cluster", "name"])
    p.add_argument("--run-dir")
    p.add_argument("--all", action="store_true", help="jobs: every paper the local model may read (default: open only)")
    p.add_argument("--label", default="")
    p.set_defaults(func=cmd_topics)

    p = sub.add_parser("saturation", help="is an induction sample big enough? kinds seen vs papers inventoried "
                       "(a G0 or template run folder; code only)")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_saturation)

    p = sub.add_parser("template", help="topic passes: route (from a topics run), inventory (jobs; then "
                       "claude-run), tally, draft, review, revise (T0); t1 (jobs; then claude-run RUN/t1), t1-summary")
    p.add_argument("action", choices=["route", "inventory", "tally", "draft", "review", "revise", "t1", "t1-summary",
                                        "t1-judge"])
    p.add_argument("--topics-run", help="route: the topics run folder")
    p.add_argument("--topic", help="route: the topic's name (or a unique part of it)")
    p.add_argument("--run-dir")
    p.add_argument("-n", type=int, default=40, help="inventory: papers to inventory (a cost cap)")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("claude-run", help="run a run folder's jobs.jsonl on the Anthropic API (key from "
                       "ANTHROPIC_API_KEY): open papers' text only; refuses a run with licensed text")
    p.add_argument("run_dir")
    p.add_argument("--model", default="claude-haiku-5-5")
    p.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    p.add_argument("-j", "--concurrency", type=int, default=8)
    p.set_defaults(func=cmd_claude_run)

    p = sub.add_parser("evals", help="every recorded eval run (eval/runs.jsonl): task, model, judge, scores")
    p.add_argument("--task")
    p.set_defaults(func=cmd_evals)

    p = sub.add_parser("models", help="the local model endpoints (profiles) and whether each is up")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("ask-local", help="ask your local model a question that needs papers an "
                       "outside model may not read; prints its answer (its words, with citations)")
    p.add_argument("question")
    p.add_argument("--key", "-k", action="append", dest="keys", help="only these papers (repeat)")
    p.add_argument("--engine", choices=["opencode", "simple"],
                   help="opencode: an agent that searches and reads (default if installed); "
                        "simple: one call with the best-matching passages")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ask_local)

    p = sub.add_parser("queue", help="batch work for a bigger model run elsewhere (JASMIN/ORCHID): "
                       "add, list, export, import, run")
    qs = p.add_subparsers(dest="qcmd", metavar="<action>", required=True)
    q = qs.add_parser("add", help="queue jobs: `summarize [KEYS] [--tag T] [--force]` or "
                      "`verify KEY CLAIM`")
    q.add_argument("kind", choices=["summarize", "verify"])
    q.add_argument("args", nargs="*")
    q.add_argument("--tag", "-t")
    q.add_argument("--force", action="store_true")
    q.add_argument("--limit", type=int)
    q.set_defaults(func=cmd_queue_add)
    q = qs.add_parser("list", help="what is queued")
    q.set_defaults(func=cmd_queue_list)
    q = qs.add_parser("export", help="write the queue to a new folder to copy to the cluster")
    q.add_argument("dir")
    q.add_argument("--model", help="model for the SLURM template (default [local_model] batch_model)")
    q.set_defaults(func=cmd_queue_export)
    q = qs.add_parser("import", help="apply a folder's results.jsonl; done jobs leave the queue")
    q.add_argument("dir")
    q.set_defaults(func=cmd_queue_import)
    q = qs.add_parser("push", help="send the queued jobs to the cluster spool as a batch "
                      "([batch_remote] in .litnav/local.toml) and submit the worker if none is queued")
    q.add_argument("--no-submit", action="store_true")
    q.set_defaults(func=cmd_queue_push)
    q = qs.add_parser("pull", help="fetch and apply results from the spool; finished batches are deleted")
    q.set_defaults(func=cmd_queue_pull)
    q = qs.add_parser("status", help="the worker job, the spool and the batches not yet pulled")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_queue_status)
    q = qs.add_parser("submit", help="submit the worker job unless one is queued or running")
    q.set_defaults(func=cmd_queue_submit)
    q = qs.add_parser("run", help="run the queue now with [local_model]")
    q.add_argument("--limit", type=int)
    q.set_defaults(func=cmd_queue_run)
    q = qs.add_parser("clear", help="empty the queue")
    q.set_defaults(func=cmd_queue_clear)


def _show(lib, rec, as_json=False):
    r = localmodel.for_agent(lib, rec)
    if as_json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    loc = r.get("location") or {}
    where = ", ".join(x for x in [loc.get("section") and f"§ {loc['section']}",
                                  loc.get("page") and f"p. {loc['page']} of the PDF text",
                                  loc.get("passage") and f"passage {loc['passage']}"
                                  + (f" (+{len(loc['passages']) - 1} more)" if len(loc.get("passages", [])) > 1 else "")]
                     if x)
    print(f"[@{r['key']}] {r['verdict'].upper()} ({r['confidence']} confidence)")
    print(f"  claim: {r['claim']}")
    if where:
        print(f"  where: {where}")
    if r.get("explanation"):
        print(f"  why:   {r['explanation']}")
    if r.get("quote"):
        print(f"  quote: {r['quote']}")
    print(f"  model: {r['model']} ({r['via']}, {r['date']})")


def cmd_verify(a):
    lib = Library.find(a.library)
    if a.log:
        p = localmodel.verify_log(lib)
        recs = [json.loads(l) for l in p.read_text("utf-8").splitlines() if l.strip()] if p.is_file() else []
        if a.key:
            k = lib.get(a.key).key
            recs = [r for r in recs if r["key"] == k]
        for r in recs:
            _show(lib, r, a.json)
            print()
        if not recs:
            print("no verdicts yet", file=sys.stderr)
        return
    if not (a.key and a.claim):
        raise ValueError("litnav verify KEY CLAIM (or --log [KEY])")
    job = localmodel.verify_job(lib, a.key, a.claim)
    if a.queue:
        n = localmodel.enqueue(lib, [job])
        print("queued" if n else "already queued", file=sys.stderr)
        return
    st = localmodel.run_now(lib, [job], log=lambda m: None)[0]
    if st["status"] != "ok":
        raise ValueError(f"verify failed: {st.get('error', st['status'])}")
    _show(lib, st["record"], a.json)


def cmd_queue_add(a):
    lib = Library.find(a.library)
    if a.kind == "verify":
        if len(a.args) != 2:
            raise ValueError("litnav queue add verify KEY CLAIM")
        jobs = [localmodel.verify_job(lib, *a.args)]
    else:
        from .. import derived
        from .. import sharing as S
        items = derived.select(lib, keys=a.args or None, tag=a.tag, force=a.force, limit=a.limit,
                               purpose=S.LOCAL_MODEL)
        jobs = [localmodel.summarize_job(it) for it in items]
    n = localmodel.enqueue(lib, jobs)
    print(f"queued {n} job(s) ({len(jobs) - n} already queued); {len(localmodel.queued(lib))} in all",
          file=sys.stderr)


def cmd_queue_list(a):
    lib = Library.find(a.library)
    jobs = localmodel.queued(lib)
    from collections import Counter
    for j in jobs:
        print(f"{j['kind']:10s} {j['key']:36s} {j.get('claim', '')[:60]}")
    print(f"{len(jobs)} job(s): " + ", ".join(f"{k} {v}" for k, v in Counter(j["kind"] for j in jobs).items()),
          file=sys.stderr)


def cmd_queue_export(a):
    lib = Library.find(a.library)
    n = localmodel.export(lib, Path(a.dir).expanduser(), model=a.model or "")
    if not n:
        print("the queue is empty", file=sys.stderr)
        return
    print(f"exported {n} job(s) to {a.dir}: copy it to the cluster, run orchid.slurm (check its "
          "#SBATCH lines), copy results.jsonl back, then `litnav queue import DIR`. The folder holds "
          "paper text: keep it on your own account and delete it afterwards.", file=sys.stderr)


def cmd_queue_import(a):
    lib = Library.find(a.library)
    c = localmodel.import_results(lib, Path(a.dir).expanduser(), log=lambda m: print(m, file=sys.stderr))
    print(", ".join(f"{k} {v}" for k, v in c.items()), file=sys.stderr)
    _reindex(lib)


def _err(m):
    print(m, file=sys.stderr)


def cmd_queue_push(a):
    from .. import spool
    lib = Library.find(a.library)
    r = spool.push(lib, submit=not a.no_submit, log=_err)
    if not r["batch"]:
        _err("nothing to push: the queue is empty, or every queued job is already in a batch")


def cmd_queue_pull(a):
    from .. import spool
    lib = Library.find(a.library)
    s = spool.pull(lib, log=_err)
    for b, v in s.items():
        print(f"{b}  {v}")
    if not s:
        _err("no batches out")
    _reindex(lib)


def cmd_queue_status(a):
    from .. import spool
    lib = Library.find(a.library)
    s = spool.status(lib)
    if a.json:
        print(json.dumps(s, indent=1))
        return
    print(f"worker job: {s['job'] or 'none queued or running'}")
    w = s["worker"]
    if w:
        print(f"worker said ({w.get('time')}, {w.get('host')}): {w.get('state')} "
              + " ".join(f"{k}={w[k]}" for k in ("batch", "jobs", "model", "note", "error") if w.get(k)))
    for b in s["spool"]:
        print(f"  {b['where']:8s} {b['batch']}  {b['results']}/{b['jobs']} results")
    for b, v in s["local"].items():
        print(f"  local    {b}  {v['state']}, {v['kind']}, {v['imported']}/{v['jobs']} imported")


def cmd_queue_submit(a):
    from .. import spool
    spool.submit_worker(Library.find(a.library), log=_err)


def cmd_queue_run(a):
    lib = Library.find(a.library)
    jobs = localmodel.queued(lib)[: a.limit] if a.limit else localmodel.queued(lib)
    st = localmodel.run_now(lib, jobs, log=lambda m: print(m, file=sys.stderr))
    done = {s["id"] for s in st if s["status"] in ("ok", "stale")}
    localmodel._rewrite_queue(lib, [j for j in localmodel.queued(lib) if j["id"] not in done])
    _reindex(lib)


def cmd_queue_clear(a):
    lib = Library.find(a.library)
    localmodel._rewrite_queue(lib, [])


def _reindex(lib):
    from .. import index
    index.update(lib, log=lambda *x: None)


def cmd_ask_local(a):
    from .. import asklocal
    lib = Library.find(a.library)
    rec = asklocal.ask(lib, a.question, keys=a.keys, engine=a.engine)
    if a.json:
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return
    print(rec["answer"])
    print(f"\n(local model: {rec['model']}, {rec['engine']}; read: {', '.join(rec['read']) or '-'}"
          + (f"; {rec['verbatim_removed']} verbatim passage(s) removed" if rec["verbatim_removed"] else "")
          + ")", file=sys.stderr)


def cmd_models(a):
    lib = Library.find(a.library)
    lib.reload_config()
    cur = lib.local_model.get("profile", "default")
    for name, cfg in lib.model_profiles.items():
        mark = "*" if name == cur else " "
        print(f"{mark} {name:10s} {cfg.get('model', '-'):40s} {cfg.get('url', '-')}")
        print(f"  {'':10s} {localmodel.ping(cfg)}")
    print("(* = in use; choose with -M NAME or $LITNAV_MODEL_PROFILE)", file=sys.stderr)


def cmd_eval_model(a):
    from .. import modeleval
    lib = Library.find(a.library)
    ev = Path(a.eval)
    if not ev.is_absolute() and not ev.exists():
        ev = lib.root / ev
    if a.action == "push":
        r = modeleval.push(lib, ev, submit=not a.no_submit, log=_err)
        _err(f"pushed {r['jobs']} call(s) as batch {r['batch']}; `litnav queue pull` brings the "
             "replies back, then `litnav eval-model report EVAL --run-dir DIR` (pull prints DIR)")
        return
    if a.run_dir:
        rd = Path(a.run_dir)
    else:
        import datetime as dt
        m = lib.local_model.get("model", "model").replace(":", "-").replace("/", "-")
        rd = lib.root / "derived" / "model-eval" / f"{dt.date.today().isoformat()}-{m}"
    if a.action == "run":
        r = modeleval.run(lib, ev, rd, limit_minutes=a.limit_minutes,
                          log=lambda m: print(m, file=sys.stderr, flush=True))
        print(json.dumps(r))
    else:
        rep = modeleval.report(lib, ev, rd)
        if a.against:
            other = modeleval.report(lib, ev, Path(a.against))
            rep["summary"]["compare"] = {"against": a.against, **modeleval.compare(rep["rows"], other["rows"])}
        (rd / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        print(json.dumps(rep["summary"], indent=1, default=str))
        from .. import papereval
        m = json.loads((rd / "run.json").read_text())
        m.setdefault("task", "verify")
        m.setdefault("created", m.get("started") or m.get("pushed", ""))
        m.setdefault("prompt_version", {"verify": localmodel.VERIFY_PROMPT_VERSION, "stepwise": "B"})
        m["eval"], m["eval_sha256"] = str(ev.relative_to(lib.root)) if ev.is_relative_to(lib.root) else str(ev), \
            papereval.sha(ev)
        (rd / "run.json").write_text(json.dumps(m, indent=1))
        s = rep["summary"]
        papereval.record(lib, rd, {"by_variant": s["by_variant"], "confusion": s["confusion"],
                                   "seconds": s["seconds"], "passage_ids_valid": s.get("passage_ids_valid")})
        _err(f"recorded in {lib.root / 'eval' / 'runs.jsonl'}")


def cmd_paper_eval(a):
    from .. import papereval as P
    lib = Library.find(a.library)
    ev = Path(a.eval) if Path(a.eval).is_absolute() else lib.root / a.eval
    log = _err
    if a.action == "pick":
        if ev.exists():
            raise ValueError(f"{ev} exists: it is the eval set; delete it to pick again")
        rows = P.pick(lib, a.n)
        ev.parent.mkdir(parents=True, exist_ok=True)
        P.save(ev, rows)
        log(f"picked {len(rows)} open papers into {ev}; next: litnav paper-eval gold")
        return
    if a.action == "gold":
        log(f"wrote key points for {P.gold(lib, ev, model=a.judge_model or 'opus', jobs=a.jobs, log=log)} paper(s)")
        return
    if a.action == "baseline":
        log(f"existing summaries: {P.baseline(lib, ev)}")
        return
    if not a.task and a.action in ("run", "push") or (not a.task and not a.run_dir):
        raise ValueError("--task summary|extract")
    if a.action == "run":
        rd = Path(a.run_dir) if a.run_dir else None
        log(f"done: {P.run(lib, ev, a.task, rd, chars=a.chars, log=log)}")
        return
    if a.action == "push":
        from .. import spool
        hint = spool.remote_cfg(lib).get("profile") or "batch"     # -M gpt-oss: its own spool and run name
        r = P.push(lib, ev, a.task, model_hint=hint, chars=a.chars, submit=not a.no_submit, log=log)
        log(f"pushed batch {r['batch']}; `litnav queue pull` brings it back")
        return
    if a.action == "rerun":
        if not (a.run_dir and a.keys):
            log("rerun needs --run-dir and at least one -k KEY")
            return 2
        r = P.push_rerun(lib, Path(a.run_dir), a.keys, submit=not a.no_submit, log=log)
        log(f"pushed {r['jobs']} job(s) again; `litnav queue pull` brings them back" if r else "no such papers in the run")
        return
    rd = Path(a.run_dir) if a.run_dir else max((lib.root / "derived" / "paper-eval").glob(f"{a.task}-*"),
                                               key=lambda p: p.stat().st_mtime)
    if a.action == "compare":
        if not (a.run_dir and a.against):
            raise ValueError("compare: --run-dir A --against B")
        ra, rb = P.report(lib, ev, rd), P.report(lib, ev, Path(a.against))
        if ra["meta"]["task"] != rb["meta"]["task"]:
            raise ValueError("compare: the two runs are different tasks")
        out = {"a": str(rd), "b": a.against, **P.compare(ra["meta"]["task"], ra["rows"], rb["rows"])}
        print(json.dumps(out, indent=1))
        return
    if a.action == "judge":
        from ..derived import Stop
        try:
            n = P.judge(lib, ev, rd, model=a.judge_model, jobs=a.jobs, judge_with=a.judge, log=log)
        except Stop as e:
            log(f"stopped: {e}. Judgments made so far are saved; rerun later to judge the rest")
            return 1
        log(f"judged {n} output(s) in {rd} ({a.judge})")
        return
    if a.action == "reask":
        if a.push:
            r = P.push_reask(lib, rd, submit=not a.no_submit, log=log)
            log(f"pushed batch {r['batch']} of re-asks" if r else "nothing to ask again")
        else:
            from .. import localmodel
            task = json.loads((rd / "run.json").read_text())["task"]
            url, model, kw = localmodel.endpoint(lib)
            n = P.reask_local(lib, rd, task, url, model, log=log, **kw)
            log(f"asked {n} call(s) again")
        return
    if a.action == "agree":
        print(json.dumps(P.agree(lib, ev, rd), indent=1))
        return
    rep = P.report(lib, ev, rd, a.judge)
    name = "report.json" if a.judge == "claude" else f"report-{a.judge}.json"
    (rd / name).write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(json.dumps(rep["summary"], indent=1))
    try:
        P.record(lib, rd, rep["summary"], judge_with=a.judge)
    except ValueError as e:
        log(f"not recorded: {e}")
        return 1
    log(f"recorded in {lib.root / 'eval' / 'runs.jsonl'}")


def cmd_evals(a):
    from .. import papereval as P
    lib = Library.find(a.library)
    reg = lib.root / "eval" / "runs.jsonl"
    if not reg.exists():
        _err("no eval runs recorded yet")
        return
    for r in P.load(reg):
        if a.task and r["task"] != a.task:
            continue
        mi = r.get("model_info") or {}
        where = mi.get("gpus") or mi.get("machine") or ""
        q = mi.get("quantization_level") or ""
        s = r["scores"]
        if r["task"] == "verify":
            sc = " ".join(f"{v}:{x['ok']}/{x['n']}" for v, x in s["by_variant"].items() if x["n"])
        else:
            sc = (f"precision {s.get('precision')} core {s.get('core_coverage')} "
                  + (f"claims/paper {s.get('claims_per_paper')}" if r["task"] == "extract" else ""))
        j = (r.get("judge") or {}).get("model", "")
        print(f"{r['date']} {r['task']:8s} {str(r['model'])[:34]:34s} {q:7s} {where[:22]:22s} {sc}"
              + (f"  (judge {j})" if j else ""))


def cmd_g0(a):
    from .. import g0
    lib = Library.find(a.library)
    if a.action == "sample":
        rd = g0.sample(lib, a.n, k=a.clusters, seed=a.seed, label=a.label, open_only=a.open_only,
                       like=Path(a.like) if a.like else None)
        print(json.dumps(json.loads((rd / "run.json").read_text())["sample"]))
        print(f"run folder {rd}; next: litnav g0 push")
        return 0
    rd = Path(a.run_dir) if a.run_dir else g0.latest(lib)
    if a.action == "jobs":
        js = g0.jobs(lib, rd)
        g0._write_jsonl(rd / "jobs.jsonl", js)
        print(f"{len(js)} job(s) in {rd / 'jobs.jsonl'}")
    elif a.action == "push":
        r = g0.push(lib, rd, submit=not a.no_submit)
        print(f"pushed {r['jobs']} job(s) as batch {r['batch']}; `litnav queue pull` brings them back")
    elif a.action == "status":
        print(json.dumps(g0.status(rd), indent=1))
    elif a.action == "tally":
        b = g0.tally(lib, rd)
        print(f"{b['papers_surveyed']} surveyed, {b['papers_inventoried']} inventoried, "
              f"{b['copied_strings_removed']} copied string(s) removed; {rd / 'bundle.json'}")
    elif a.action == "synthesize":
        g0.synthesize(lib, rd, model=a.model or g0.FABLE, n_open=a.open, open_chars=a.open_chars)
        print(f"draft written: {g0.drafts(rd)[-1]}")
    elif a.action == "review":
        r = g0.review(lib, rd)
        for w, v in r.items():
            print(f"{w}: {v['verdict']}, {len(v['issues'])} issue(s)")
    elif a.action == "revise":
        g0.revise(lib, rd, model=a.model or g0.FABLE)
        print(f"draft written: {g0.drafts(rd)[-1]}")
    return 0


def cmd_claude_run(a):
    from .. import g0
    lib = Library.find(a.library)
    n = g0.run_outside(lib, Path(a.run_dir), model=a.model, effort=a.effort, concurrency=a.concurrency)
    print(f"{n} job(s) done")
    return 0


def cmd_model_registry(a):
    from .. import modelreg
    lib = Library.find(a.library)
    if a.action == "jobs":
        rd = modelreg.new_run(lib, open_only=not a.all, label=a.label)
        print(f"{json.loads((rd / 'run.json').read_text())['papers']} job(s) in {rd}")
        return 0
    rd = Path(a.run_dir)
    if a.action == "build":
        reg = modelreg.build(rd)
        print(f"{reg['papers']} papers; {len(reg['cases'])} model configurations "
              f"({sum(c['deep_convection'] in ('parametrized', 'explicit') for c in reg['cases'])} stating their "
              f"deep-convection treatment); {len(reg['definitions'])} definitions")
    elif a.action == "score":
        reg = modelreg.load(rd)
        rows = [modelreg.score_stated(reg, m) for m in ("bracket", "exact", ("factor", 1.5), ("factor", 2.0),
                                                        ("factor", 4.0))]
        (rd / "score.json").write_text(json.dumps(rows, indent=1))
        print(json.dumps(rows, indent=1))
    else:
        voc = modelreg.induce_classes(rd)
        for c in voc["classes"]:
            print(f"{c['id']}: {c['deep_convection']}; {len(c['papers'])} papers; span {c['span_m']}; "
                  f"terms {c['terms'][:6]}")
    return 0


def cmd_topics(a):
    from .. import topics
    lib = Library.find(a.library)
    if a.action == "jobs":
        rd = topics.new_run(lib, open_only=not a.all, label=a.label)
        print(f"{json.loads((rd / 'run.json').read_text())['papers']} job(s) in {rd}")
    elif a.action == "cluster":
        c = topics.cluster(lib, Path(a.run_dir))
        print(f"{c['aspects']} aspects from {c['papers']} papers; {len(c['clusters'])} recurring clusters; "
              f"{c['singletons']} aspects in no recurring cluster")
        for x in c["clusters"][:40]:
            print(f"  {x['id']} {x['papers']:3d} papers  {'; '.join(x['sample'][:4])}")
    else:
        t = topics.name(Path(a.run_dir))
        for x in t["topics"]:
            print(f"{x['papers']:3d} papers  {x['name']} ({x['kind']}): {x['description']}")
    return 0


def cmd_template(a):
    from .. import templates as T
    lib = Library.find(a.library)
    if a.action == "route":
        rd, r = T.route(lib, Path(a.topics_run), a.topic)
        print(f"{r['topic']}: {len(r['papers'])} open papers routed; {rd}")
        return 0
    rd = Path(a.run_dir)
    if a.action == "inventory":
        print(f"{len(T.inventory_jobs(lib, rd, n=a.n))} inventory job(s) in {rd / 'jobs.jsonl'}; next: litnav claude-run {rd}")
    elif a.action == "tally":
        t = T.tally(lib, rd)
        print(f"{t['reporting_topic']} of {t['papers_inventoried']} papers report the topic; {len(t['kinds'])} kinds of detail")
        for k in t["kinds"][:25]:
            print(f"  {k['papers']:3d} papers ({k['with_value']} with a value)  {'; '.join(k['names'][:3])}")
    elif a.action in ("draft", "revise"):
        out = (T.draft if a.action == "draft" else T.revise)(rd)
        for f in out["fields"]:
            print(f"  {f['name']} ({f['applies_to']}, {f['form']}): {f.get('n_papers', '?')} papers"
                  + (f"; numbers not in the evidence: {f['numbers_not_in_evidence']}" if f.get("numbers_not_in_evidence") else ""))
    elif a.action == "review":
        for w, v in T.review(rd).items():
            print(f"{w}: {v['verdict']}, {len(v['issues'])} issue(s)")
    elif a.action == "t1":
        out = T.t1_jobs(lib, rd)
        print(f"T1 jobs in {out}; next: litnav claude-run {out}")
    elif a.action == "t1-judge":
        s = T.judge_t1(lib, rd, n=a.n if a.n != 40 else 5)
        print(f"{len(s['papers'])} papers, {s['values']} values: {s['verdicts']}; missed {s['missed']}")
    else:
        print(json.dumps(T.t1_summary(rd), indent=1))
    return 0


def cmd_saturation(a):
    from .. import saturation as SAT
    out = SAT.run(Library.find(a.library), Path(a.run_dir))
    for name, by in out["items"].items():
        for agree, c in by.items():
            marks = [x for x in c["recurring_curve"] if x["n"] in (5, 10, 20, 40, 80, c["papers"])]
            print(f"{name} (agree {agree}): {c['papers']} papers, {c['kinds']} kinds ({c['recurring']} recurring); "
                  f"recurring seen at n=" + ", ".join(f"{x['n']}: {x['mean']:g}" for x in marks)
                  + f"; new recurring kinds per paper over the last fifth: {c['new_recurring_per_paper_last_fifth']}")
    return 0
