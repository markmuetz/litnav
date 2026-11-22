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
    p.add_argument("action", choices=["run", "report"])
    p.add_argument("eval", help="eval set (JSONL), e.g. eval/model-claims.jsonl")
    p.add_argument("--run-dir", help="default derived/model-eval/<date>-<model>")
    p.add_argument("--limit-minutes", type=float, default=88)
    p.set_defaults(func=cmd_eval_model)

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
        (rd / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        print(json.dumps(rep["summary"], indent=1, default=str))
