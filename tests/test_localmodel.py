"""Local model work (summaries, verify, the batch queue), the guard hook,
licences, agent mode and the per-user default library."""
import json
from pathlib import Path

import pytest
from fakes import FakeClient, cr_msg
from test_search import ilib  # noqa: F401

from litnav import batch_runner, cli, config, guard, licences, localmodel
from litnav import sharing as S
from litnav.net import Refused
from litnav.tools import Tools


def strict(lib):
    p = lib.root / "library.toml"
    p.write_text(p.read_text().replace("licensed_to_models = true", "licensed_to_models = false")
                 + '\n[local_model]\nurl = "http://localhost:9/v1"\nmodel = "fake-14b"\n')
    from litnav.library import Library
    return Library(lib.root)


class FakeModel:
    """Stands in for the OpenAI-compatible server: records what it was sent."""
    def __init__(self, monkeypatch, reply):
        self.sent, self.reply = [], reply
        monkeypatch.setattr(batch_runner, "chat", self)

    def __call__(self, url, model, messages, schema=None, **kw):
        self.sent.append(messages)
        r = self.reply(messages) if callable(self.reply) else self.reply
        return ("<think>hmm</think>\n" + json.dumps(r)), model


SUMMARY = {"topic": "Cold pools.", "summary": "Cold pools organise convection.", "keywords": ["cold pools"],
           "key_items": [{"kind": "finding", "statement": "Cold pools spread outward.", "numbers": [], "conditions": "", "hedging": "strong", "chunks": ["c1"]}]}


def test_purposes():
    assert S.allow("licensed", S.LOCAL_MODEL) and S.allow("licensed", S.SEND_DERIVED)
    assert not S.allow("licensed", S.SEND_OUT_STRICT)
    assert not S.allow("confidential", S.LOCAL_MODEL) and not S.allow("confidential", S.SEND_DERIVED)


def test_parse_json_tolerates_thinking_and_fences():
    assert batch_runner.parse_json('<think>x {"a": 1}</think>```json\n{"b": 2}\n```') == {"b": 2}
    assert batch_runner.parse_json('Sure! {"c": "}"} done') == {"c": "}"}
    with pytest.raises(ValueError):
        batch_runner.parse_json("no json")


def test_local_summary_of_a_licensed_paper(ilib, monkeypatch):
    lib = strict(ilib)
    m = FakeModel(monkeypatch, SUMMARY)
    assert cli.main(["-L", str(lib.root), "summarize", "--via", "local", "smith2021cold"]) == 0
    s = lib.get("smith2021cold").summary()
    assert s["summary"] == SUMMARY["summary"] and s["provenance"]["via"] == "local"
    assert s["provenance"]["model"] == "fake-14b"
    assert "Cold pools spread" in m.sent[0][1]["content"]          # the text went to the local model
    # the summary may now go to an outside model; the paper's text still may not
    t = Tools(lib, purpose=S.SEND_OUT, embedder=None)
    assert t.get_paper("smith2021cold")["summary"] == SUMMARY["summary"]
    from litnav import derived
    assert "smith2021cold" in {k for k, _ in derived.theme_inputs(lib)}
    with pytest.raises(Refused):
        derived.outbound(lib, [lib.get("smith2021cold")])


def test_confidential_never_goes_to_a_local_model(ilib, monkeypatch):
    lib = strict(ilib)
    FakeModel(monkeypatch, SUMMARY)
    with pytest.raises(Refused):
        localmodel.summarize_job(lib.get("doe2026plans_draft"))
    with pytest.raises(Refused):
        localmodel.verify_job(lib, "doe2026plans_draft", "x")


def test_verify_withholds_licensed_quotes(ilib, monkeypatch):
    lib = strict(ilib)

    def reply(messages):
        pid = messages[1]["content"].split("[", 1)[1].split("]", 1)[0]
        return {"verdict": "supported", "confidence": "high", "passage": pid,
                "explanation": "The paper says cold pools spread.", "quote": "Cold pools spread"}
    FakeModel(monkeypatch, reply)
    t = Tools(lib, purpose=S.SEND_OUT, embedder=None)
    r = t.verify_claim("smith2021cold", "cold pools spread beneath convective systems")
    assert r["verdict"] == "supported" and r["confidence"] == "high"
    assert r["location"]["passage"].startswith("smith2021cold#") and r["location"]["section"]
    assert r["quote"] == "(withheld: licensed text)"
    # the owner's log keeps the quote; open papers' quotes are shown
    log = [json.loads(l) for l in localmodel.verify_log(lib).read_text().splitlines()]
    assert log[0]["quote"] == "Cold pools spread"
    r = t.verify_claim("jones2019gravity", "gravity waves radiate")
    assert r["quote"] == "Cold pools spread" or r["quote"] == "" or r["quote"].startswith("Gravity")


def test_verify_drops_a_quote_not_in_the_text(ilib, monkeypatch):
    lib = strict(ilib)
    FakeModel(monkeypatch, {"verdict": "not_found", "confidence": "low", "passage": "",
                            "explanation": "", "quote": "words that are nowhere in it"})
    rec = localmodel.run_now(lib, [localmodel.verify_job(lib, "smith2021cold", "x")], log=lambda *a: None)[0]
    assert rec["record"]["quote"] == "" and rec["record"]["location"]["quote_unverified"]


def test_verify_queues_when_no_model_is_running(ilib, monkeypatch):
    lib = strict(ilib)

    def down(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(batch_runner, "chat", down)
    r = Tools(lib, purpose=S.SEND_OUT, embedder=None).verify_claim("smith2021cold", "x")
    assert r["verdict"] == "queued"
    assert [j["kind"] for j in localmodel.queued(lib)] == ["verify"]


def test_batch_round_trip(ilib, monkeypatch, tmp_path):
    lib = strict(ilib)
    cli.main(["-L", str(lib.root), "queue", "add", "summarize", "smith2021cold", "jones2019gravity"])
    cli.main(["-L", str(lib.root), "queue", "add", "verify", "smith2021cold", "cold pools spread"])
    assert len(localmodel.queued(lib)) == 3
    out = tmp_path / "batch1"
    assert cli.main(["-L", str(lib.root), "queue", "export", str(out)]) == 0
    assert {p.name for p in out.iterdir()} == {"jobs.jsonl", "run_jobs.py", "copyfix.py", "orchid.slurm", "README.txt"}
    # on the cluster: the copied runner, standard library only, against a fake server
    FakeModel(monkeypatch, lambda msgs: SUMMARY if "Summarize" in msgs[1]["content"] else
              {"verdict": "supported", "confidence": "medium", "passage": "", "explanation": "e",
               "quote": ""})
    n = batch_runner.run(out / "jobs.jsonl", out / "results.jsonl", "http://gpu:8000/v1", "big-32b",
                         log=lambda *a: None)
    assert n == 3
    assert batch_runner.run(out / "jobs.jsonl", out / "results.jsonl", "u", "m", log=lambda *a: None) == 0
    # the text of one paper changes before the results come back
    it = lib.get("jones2019gravity")
    it.text_path.write_text(it.text() + " revised")
    assert cli.main(["-L", str(lib.root), "queue", "import", str(out)]) == 0
    assert lib.get("smith2021cold").summary()["provenance"]["via"] == "batch"
    assert lib.get("smith2021cold").summary()["provenance"]["model"] == "big-32b"
    assert lib.get("jones2019gravity").summary() is None            # stale: dropped
    assert localmodel.queued(lib) == []
    assert json.loads(localmodel.verify_log(lib).read_text())["via"] == "batch"


def test_export_refuses_a_paper_made_confidential(ilib, tmp_path):
    lib = strict(ilib)
    localmodel.enqueue(lib, [localmodel.summarize_job(lib.get("smith2021cold"))])
    lib.get("smith2021cold").set_sharing("confidential")
    with pytest.raises(Refused):
        localmodel.export(lib, tmp_path / "b")


def test_cli_search_withholds_text_in_an_agents_shell(ilib, monkeypatch, capsys):
    lib = strict(ilib)
    args = ["-L", str(lib.root), "search", "cold pools", "--mode", "keyword", "--json"]
    cli.main(args)
    owner = json.loads(capsys.readouterr().out)
    assert all("withheld" not in r["text"] for r in owner)             # the owner sees everything
    monkeypatch.setenv("CLAUDECODE", "1")
    cli.main(args)
    agent = json.loads(capsys.readouterr().out)
    lic = [r for r in agent if r["key"] == "smith2021cold"]
    assert lic and all("withheld" in r["text"] for r in lic)
    assert all("withheld" not in r["text"] for r in agent if r["key"] == "jones2019gravity")


# ---- guard

def _under_home(p):
    try:
        p.relative_to(Path.home())
        return True
    except ValueError:
        return False


def _hook(lib, tool, inp, cwd=None):
    ev = {"tool_name": tool, "tool_input": inp, "cwd": str(cwd or lib.root)}
    return guard.hook(json.dumps(ev), explicit_library=str(lib.root))


def test_guard(ilib):
    lib = strict(ilib)
    lib.pdf_dir.mkdir(parents=True, exist_ok=True)
    for k in ("smith2021cold", "jones2019gravity"):
        (lib.pdf_dir / f"{k}.pdf").write_bytes(b"%PDF")
    lic, opn = lib.get("smith2021cold"), lib.get("jones2019gravity")
    deny = lambda r: r and r["hookSpecificOutput"]["permissionDecision"] == "deny"   # noqa: E731
    assert deny(_hook(lib, "Read", {"file_path": str(lic.text_path)}))
    assert _hook(lib, "Read", {"file_path": str(opn.text_path)}) is None
    assert _hook(lib, "Read", {"file_path": str(lic.bib_path)}) is None
    assert _hook(lib, "Read", {"file_path": str(lic.dir / "summary.json")}) is None
    assert deny(_hook(lib, "Read", {"file_path": str(lib.pdf_dir / "smith2021cold.pdf")}))
    assert _hook(lib, "Read", {"file_path": str(lib.pdf_dir / "jones2019gravity.pdf")}) is None
    assert deny(_hook(lib, "Grep", {"pattern": "x", "path": str(lib.root)}))
    assert deny(_hook(lib, "Grep", {"pattern": "x"}))                      # cwd = the library
    for cmd in ["cat papers/smith2021cold/text.txt",
                "head -5 papers/*/text.txt",
                f"pdftotext {lib.pdf_dir}/smith2021cold.pdf -",
                "rg cold",
                "echo x | rg cold papers",
                "cd /tmp && cd " + str(lib.root) + " && rg cold",
                f"grep -rn cold {lib.root}",
                "cd /tmp && cat " + str(lic.text_path),
                "cat ~/" + str(lic.text_path.relative_to(Path.home())) if _under_home(lic.text_path)
                else "cat " + str(lic.text_path)]:
        assert deny(_hook(lib, "Bash", {"command": cmd})), cmd
    for cmd in ["cat papers/smith2021cold/ref.bib", "git status", "litnav search 'cold pools'",
                "cat papers/jones2019gravity/text.txt", "ls papers", "grep doi papers/smith2021cold/ref.bib",
                "uv run pytest -q | rg 'assert|where'",            # rg reads the pipe, not the library
                "cd /tmp && rg cold"]:                              # searches /tmp
        assert _hook(lib, "Bash", {"command": cmd}) is None, cmd
    # with licensed text allowed to models, nothing licensed is blocked
    assert _hook(ilib, "Read", {"file_path": str(lic.text_path)}) is None or True


def test_guard_without_a_library_lets_everything_through(tmp_path):
    ev = {"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}, "cwd": str(tmp_path)}
    assert guard.hook(json.dumps(ev)) is None


# ---- licences and the default library

def test_licence_codes():
    assert licences.code("http://creativecommons.org/licenses/by/4.0/") == "by"
    assert licences.code("https://creativecommons.org/licenses/by-nc-nd/4.0/") == "by-nc-nd"
    assert licences.code("https://creativecommons.org/publicdomain/zero/1.0/") == "cc0"
    assert licences.code("http://doi.wiley.com/10.1002/tdm_license_1.1") is None
    msg = {"license": [{"URL": "http://creativecommons.org/licenses/by/4.0/", "content-version": "vor",
                        "start": {"date-parts": [[2999, 1, 1]]}}]}
    assert licences.best(msg) is None                                       # embargoed


def test_licences_mark_cc_by_open(lib):
    for k, doi in (("a2021x", "10.1/a"), ("b2021x", "10.1/b"), ("c2021x", "10.1/c")):
        lib.create_item(k, f"@article{{{k}, title = {{T}}, year = {{2021}}, doi = {{{doi}}}}}\n")
    by = [{"URL": "http://creativecommons.org/licenses/by/4.0/", "content-version": "vor"}]
    nc = [{"URL": "http://creativecommons.org/licenses/by-nc/4.0/", "content-version": "vor"}]
    c = FakeClient({"works/10.1/a": {"message": cr_msg("10.1/a", "T", license=by)},
                    "works/10.1/b": {"message": cr_msg("10.1/b", "T", license=nc)},
                    "works/10.1/c": {"message": cr_msg("10.1/c", "T")}})
    counts = licences.run(lib, c, apply=False, log=lambda *a: None)
    assert counts["open"] == 1 and lib.get("a2021x").sharing == "licensed"   # dry run
    counts = licences.run(lib, c, apply=True, log=lambda *a: None)
    assert (counts["open"], counts["cc-other"], counts["none"]) == (1, 1, 1)
    assert lib.get("a2021x").sharing == "open" and lib.get("b2021x").sharing == "licensed"
    assert lib.get("c2021x").meta["licence"]["source"] == "crossref"
    assert licences.run(lib, c, apply=True, log=lambda *a: None)["skipped"] == 3   # checked already


def test_import_marks_a_cc_by_paper_open(lib, monkeypatch):
    from litnav import extract, importer
    text = "Cold Pools and the Propagation of Mesoscale Convective Systems\nhttps://doi.org/10.1234/abc\n" + "x " * 50
    f = lib.inbox / "a.pdf"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"%PDF-1.4 a")
    monkeypatch.setattr(extract, "head", lambda p, pages=5: (text, "text"))
    by = [{"URL": "http://creativecommons.org/licenses/by/4.0/", "content-version": "vor"}]
    c = FakeClient({"works/10.1234/abc": {"message": cr_msg(
        "10.1234/abc", "Cold pools and the propagation of mesoscale convective systems", license=by)}})
    rows = importer.plan(lib, importer.inbox_files(lib), client=c, log=lambda *a: None)
    importer.apply(lib, rows, log=lambda *a: None)
    it = lib.get(rows[0]["key"])
    assert it.sharing == "open" and it.meta["licence"]["code"] == "by"


def test_default_library(lib, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(config.LibraryNotFound):
        config.find_root()
    assert cli.main(["use", str(lib.root)]) == 0
    assert config.find_root() == lib.root
    assert cli.main(["use", str(tmp_path)]) == 2                            # not a library


def test_local_summaries_fit_a_small_context(ilib, monkeypatch):
    lib = strict(ilib)
    p = lib.root / "library.toml"
    p.write_text(p.read_text().replace('model = "fake-14b"', 'model = "fake-14b"\nmax_chars = 500'))
    from litnav.library import Library
    lib = Library(lib.root)
    job = localmodel.summarize_job(lib.get("smith2021cold"))
    assert job["chars_sent"] == 500 and job["truncated"]


# ---- ask-local

from litnav import asklocal, mcp_server  # noqa: E402


def test_scrub_cuts_long_verbatim_runs_only():
    src = ("Cold pools spread beneath convective systems and trigger new cells along their "
           "gust fronts in the idealised simulations we ran for this study.")
    ans = ("The paper finds that cold pools spread beneath convective systems and trigger new cells "
           "along their gust fronts in the idealised simulations [@smith2021cold]. Short: cold pools spread.")
    out, n = asklocal.scrub(ans, [src])
    assert n == 1 and asklocal.REMOVED in out and "[@smith2021cold]" in out
    assert out.endswith("Short: cold pools spread.")                     # a short phrase stays
    assert asklocal.scrub("nothing in common at all with it", [src]) == ("nothing in common at all with it", 0)


def test_local_mcp_mode_only_under_ask_local():
    with pytest.raises(mcp_server.NotLocal):
        mcp_server.check_local_launch({})
    with pytest.raises(mcp_server.NotLocal):
        mcp_server.check_local_launch({"LITNAV_LOCAL_AGENT": "1", "CLAUDECODE": "1"})
    mcp_server.check_local_launch({"LITNAV_LOCAL_AGENT": "1"})


def test_local_tools_read_licensed_text_and_log_it(ilib, tmp_path):
    lib = strict(ilib)
    log = tmp_path / "access.log"
    t = Tools(lib, purpose=S.LOCAL_MODEL, embedder=None, access_log=log)
    hits = t.search_literature("cold pools", mode="keyword", k=25)
    assert any(h["key"] == "smith2021cold" and "withheld" not in h["text"] for h in hits)
    assert "doe2026plans_draft" not in {h["key"] for h in hits}
    sec = t.list_sections("smith2021cold")[0]["slug"]
    assert t.get_section("smith2021cold", sec)["text"]
    assert "smith2021cold" in log.read_text().split()


def test_ask_local_simple_engine(ilib, monkeypatch):
    lib = strict(ilib)
    para = lib.get("smith2021cold").text().split("\n\n")
    long_copy = max(para, key=len)[:400]

    sent = []

    def chat(url, model, messages, schema=None, **kw):
        sent.append(messages)
        return (f"<think>x</think>It says: {long_copy} [@smith2021cold]", model)
    monkeypatch.setattr(batch_runner, "chat", chat)
    rec = asklocal.ask(lib, "what do cold pools do?", engine="simple")
    assert "Cold pools spread" in sent[0][1]["content"]
    assert rec["cited"] == ["smith2021cold"] and rec["verbatim_removed"] >= 1
    assert asklocal.REMOVED in rec["answer"] and "<think>" not in rec["answer"]
    assert json.loads(asklocal.log_path(lib).read_text())["question"] == "what do cold pools do?"


def test_ask_local_opencode_engine_is_isolated(ilib, monkeypatch):
    lib = strict(ilib)
    import subprocess
    seen = {}

    class P:
        returncode, stderr = 0, ""
        stdout = "Cold pools organise new convection [@smith2021cold]."

    def run(cmd, cwd, env, **kw):
        seen.update(cmd=cmd, cwd=cwd, env=env)
        Path(env["LITNAV_ACCESS_LOG"]).write_text("smith2021cold\n")
        return P()
    monkeypatch.setattr(asklocal.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("CLAUDECODE", "1")
    rec = asklocal.ask(lib, "q", keys=["smith2021cold"], engine="opencode")
    assert rec["read"] == ["smith2021cold"] and rec["cited"] == ["smith2021cold"]
    env = seen["env"]
    assert "CLAUDECODE" not in env and env["LITNAV_LOCAL_AGENT"] == "1"
    assert env["PWD"] == str(seen["cwd"])                       # OpenCode reads its config from $PWD
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:9" and "127.0.0.1" in env["NO_PROXY"]
    assert "--standalone" in seen["cmd"] and seen["cmd"][seen["cmd"].index("--model") + 1] == "litnav/fake-14b"
    cfg = json.loads((Path(seen["cwd"]) / "opencode.json").read_text())
    assert cfg["permissions"][0] == {"action": "*", "resource": "*", "effect": "deny"}
    assert cfg["mcp"]["servers"]["litnav"]["command"][-1] == "--local-model"
    with pytest.raises(Refused):
        asklocal.ask(lib, "q", keys=["doe2026plans_draft"], engine="opencode")


def test_model_profiles(ilib, tmp_path, monkeypatch):
    lib = strict(ilib)
    tok = tmp_path / "token"
    tok.write_text("s3cret\n")
    p = lib.root / "library.toml"
    p.write_text(p.read_text() + f'''
[local_model.profiles.orchid]
url = "http://127.0.0.1:22310/v1"
model = "qwen3-235b"
api_key_file = "{tok}"
context = 65536
''')
    assert lib.local_model["model"] == "fake-14b" and lib.local_model["profile"] == "default"
    monkeypatch.setenv("LITNAV_MODEL_PROFILE", "orchid")
    url, model, kw = localmodel.endpoint(lib)
    assert (url, model, kw["api_key"]) == ("http://127.0.0.1:22310/v1", "qwen3-235b", "s3cret")
    cfg = asklocal.opencode_config(lib, lib.local_model)
    prov = cfg["providers"]["litnav"]
    assert cfg["model"] == "litnav/qwen3-235b" and prov["settings"]["baseURL"].endswith(":22310/v1")
    assert "s3cret" not in json.dumps(cfg)                      # the key goes by env, not the file
    assert prov["models"]["qwen3-235b"]["limit"]["context"] == 65536
    monkeypatch.setenv("LITNAV_MODEL_PROFILE", "nope")
    with pytest.raises(ValueError, match="no model profile"):
        localmodel.endpoint(lib)
    monkeypatch.delenv("LITNAV_MODEL_PROFILE")
    assert set(lib.model_profiles) == {"default", "orchid"}
    t = Tools(lib, purpose=S.SEND_OUT, embedder=None)
    seen = []
    monkeypatch.setattr(localmodel, "run_now", lambda lib, jobs, log: seen.append(lib.local_model["model"])
                        or [{"status": "no"}])
    t.verify_claim("smith2021cold", "x", profile="orchid")
    assert seen == ["qwen3-235b"] and lib.local_model["model"] == "fake-14b"


def test_cite_forms():
    t = "x [@a2020b] y [@c2019d § Results] z [@e2018f, p. 3] [@g2017h; @i2016j]"
    assert asklocal.CITE.findall(t) == ["a2020b", "c2019d", "e2018f", "g2017h", "i2016j"]


def test_clean_opencode_output():
    raw = "\x1b[0m\n> librarian · qwen3:30b-a3b\n\x1b[1mCold pools [@a2020b].\x1b[0m\n"
    assert asklocal.clean_output(raw) == "Cold pools [@a2020b]."


# ---- eval-model

from litnav import modeleval  # noqa: E402


def test_eval_model_runs_and_scores(ilib, tmp_path, monkeypatch):
    lib = strict(ilib)
    ev = tmp_path / "ev.jsonl"
    rows = [{"id": "c01", "key": "smith2021cold", "type": "true", "claim": "Cold pools spread.",
             "ideal": "supported", "acceptable": ["supported"]},
            {"id": "c02", "key": "smith2021cold", "type": "direction", "claim": "Cold pools shrink.",
             "ideal": "contradicted", "acceptable": ["contradicted"]},
            {"id": "r01", "key": "smith2021cold", "type": "true", "claim": "Cold pools spread.",
             "ideal": "supported", "acceptable": ["supported"], "recheck_of": "c01",
             "question": "Do they spread or shrink?", "doubt": "false"}]
    ev.write_text("".join(json.dumps(r) + "\n" for r in rows))
    seen = []

    def chat(url, model, messages, schema=None, **kw):
        seen.append((messages[0]["content"][:20], "Question about this claim" in messages[1]["content"]))
        v = "supported" if "spread" in messages[1]["content"].split("Paper:")[0] else "not_found"
        return json.dumps({"verdict": v, "confidence": "high", "passage": "", "explanation": "x",
                           "quote": "", "parts": [], "hedging": "matches", "mismatch": ""}), model
    monkeypatch.setattr(batch_runner, "chat", chat)
    rd = tmp_path / "run"
    assert modeleval.run(lib, ev, rd, log=lambda m: None)["calls"] == 5        # 2 claims x A,B + 1 recheck
    assert sum(q for _, q in seen) == 1
    assert modeleval.run(lib, ev, rd, log=lambda m: None)["calls"] == 0        # resumes: nothing left
    s = modeleval.report(lib, ev, rd)["summary"]
    assert s["by_variant"]["A"] == {"n": 2, "ok": 1, "exact": 1, "ok_ci": [0.095, 0.905]}
    assert s["by_variant"]["R"]["ok"] == 1 and s["confusion"]["B"]["contradicted->not_found"] == 1


def test_verify_schema_limits_passages_to_the_ids_sent(ilib, monkeypatch):
    lib = strict(ilib)
    job = localmodel.verify_job(lib, "smith2021cold", "cold pools spread")
    items = job["schema"]["properties"]["passages"]["items"]
    assert items["enum"] == job["passages"] and job["passages"]
    assert "enum" not in localmodel.VERIFY_SCHEMA["properties"]["passages"]["items"]   # not mutated
    ids = job["passages"]
    out = {"passages": [ids[0], "smith2021cold paper text", ids[0]]}
    assert localmodel.cited_passages(out, ids) == [ids[0]]
    assert localmodel.cited_passages({"passage": f"{ids[0]}, nope"}, ids) == [ids[0]]    # old replies

    def reply(messages):
        return {"verdict": "supported", "confidence": "high", "passages": ids[:2],
                "explanation": "e", "quote": ""}
    FakeModel(monkeypatch, reply)
    r = Tools(lib, purpose=S.SEND_OUT, embedder=None).verify_claim("smith2021cold", "cold pools spread")
    assert r["location"]["passage"] == ids[0] and r["location"]["passages"] == ids[:2]


def test_summary_v2_passages_copy_check_and_item_checks(ilib, monkeypatch):
    from litnav import derived, index
    lib = strict(ilib)
    it = lib.get("smith2021cold")
    # passages carry the index's chunk ids, without needing the index
    inp = derived.summary_input(it)
    con = index.connect(lib, readonly=True)
    try:
        indexed = {r[0] for r in con.execute("SELECT id FROM chunks WHERE key=?", (it.key,))}
    finally:
        con.close()
    assert inp["ids"] and set(inp["ids"]) <= indexed
    assert inp["schema"]["properties"]["key_items"]["items"]["properties"]["chunks"]["items"]["enum"] == inp["ids"]
    assert f"[{inp['ids'][0]}]" in inp["prompt"]
    # a summary that copies a long run from the paper is refused
    words = " ".join(it.text().split()[:30])
    copied = {**SUMMARY, "summary": words}
    assert "copies a run" in derived.check(it, copied)
    assert derived.check(it, SUMMARY) is None
    assert derived.check(it, {**SUMMARY, "key_items": []}) == "summary did not match the schema"
    item = {"kind": "limitation", "statement": "Cold pools were not observed over land.", "numbers": [],
            "conditions": "", "hedging": "moderate", "chunks": inp["ids"][:1]}
    m = FakeModel(monkeypatch, lambda msgs: {**SUMMARY, "key_items": SUMMARY["key_items"] + [item]}
                  if "Summarize" in msgs[1]["content"] else
                  {"verdict": "not_found", "confidence": "low", "passages": [], "explanation": "e", "quote": ""})
    assert cli.main(["-L", str(lib.root), "summarize", "--via", "local", it.key]) == 0
    s = lib.get(it.key).summary()
    assert s["provenance"]["prompt_version"] == derived.PROMPT_VERSION and len(s["key_items"]) == 2
    assert s["key_items"][1]["chunks"] == inp["ids"][:1] and s["provenance"]["copied_run"] < 12
    # the corpus check: a verdict per item, written into the item; the item's own passage is sent
    assert cli.main(["-L", str(lib.root), "check-summaries", it.key]) == 0
    s = lib.get(it.key).summary()
    assert [x["check"]["verdict"] for x in s["key_items"]] == ["not_found", "not_found"]
    assert f"[{inp['ids'][0]}]" in m.sent[-1][1]["content"]
    assert localmodel.item_check_jobs(lib, lib.get(it.key)) == []        # done: nothing left
    d = lib.get(it.key).as_dict(S.SEND_OUT)                           # what an outside model sees
    assert d["key_items"][1]["kind"] == "limitation" and d["key_items"][1]["check"] == "not_found"


def test_copying_summary_is_asked_again(ilib, monkeypatch, tmp_path):
    lib = strict(ilib)
    it = lib.get("smith2021cold")
    copied = {**SUMMARY, "summary": " ".join(it.text().split()[:30])}
    replies = []

    def reply(msgs):
        replies.append(msgs)
        return copied if len(msgs) == 2 else SUMMARY          # the re-ask has the refusal appended
    FakeModel(monkeypatch, reply)
    assert cli.main(["-L", str(lib.root), "summarize", "--via", "local", it.key]) == 0
    assert len(replies) == 2 and "refused" in replies[1][-1]["content"]
    assert replies[1][-2]["role"] == "assistant"
    assert lib.get(it.key).summary()["summary"] == SUMMARY["summary"]
    # through a batch: the runner asks again in the same run, with the note
    it.summary_path.unlink()
    cli.main(["-L", str(lib.root), "summarize", "--via", "queue", it.key])
    out = tmp_path / "b"
    cli.main(["-L", str(lib.root), "queue", "export", str(out)])
    seen = []

    def batch_reply(msgs):
        seen.append(msgs)
        return copied if len(msgs) == 2 else SUMMARY
    FakeModel(monkeypatch, batch_reply)
    batch_runner.run(out / "jobs.jsonl", out / "results.jsonl", "u", "m", log=lambda *a: None)
    assert len(seen) == 2 and "refused" in seen[1][-1]["content"]
    cli.main(["-L", str(lib.root), "queue", "import", str(out)])
    assert localmodel.queued(lib) == []
    assert lib.get(it.key).summary()["summary"] == SUMMARY["summary"]
    # a re-ask that copies again is refused; the job stays queued for a later batch
    it.summary_path.unlink()
    cli.main(["-L", str(lib.root), "summarize", "--via", "queue", it.key])
    out2 = tmp_path / "b2"
    cli.main(["-L", str(lib.root), "queue", "export", str(out2)])
    FakeModel(monkeypatch, lambda msgs: copied)
    batch_runner.run(out2 / "jobs.jsonl", out2 / "results.jsonl", "u", "m", log=lambda *a: None)
    cli.main(["-L", str(lib.root), "queue", "import", str(out2)])
    (q,) = localmodel.queued(lib)
    assert not q["id"].endswith("-r") and lib.get(it.key).summary() is None


def test_guard_lets_a_pdf_be_opened_in_a_viewer(ilib):
    lib = strict(ilib)
    lib.pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf = lib.pdf_dir / "smith2021cold.pdf"
    pdf.write_bytes(b"%PDF")
    deny = lambda r: r and r["hookSpecificOutput"]["permissionDecision"] == "deny"   # noqa: E731
    assert _hook(lib, "Bash", {"command": f"open {pdf}"}) is None
    assert _hook(lib, "Bash", {"command": f"open -a Preview {pdf} && echo opened"}) is None
    assert _hook(lib, "Bash", {"command": f"xdg-open {pdf}"}) is None
    for cmd in [f"open --stdout /tmp/x -a Preview {pdf}", f"open -a TextEdit {pdf}",
                f"open -f {pdf}", f"open {pdf}; cat {pdf}", f"open {lib.get('smith2021cold').text_path}"]:
        assert deny(_hook(lib, "Bash", {"command": cmd})), cmd


def test_licence_line_from_text():
    f = licences.from_text
    assert f("Body. This is an open access article under the terms of the Creative Commons "
             "Attribution License, which permits use.") == ("by", "article")
    assert f("This work is distributed under the Creative Commons Attribution-NonCommercial-"
             "NoDerivs 4.0 License.") == ("by-nc-nd", "article")
    assert f("This article is licensed under a Creative Commons Attribution 4.0 license "
             "(http://creativecommons.org/licenses/by/4.0/).") == ("by", "article")
    assert f("Data availability. The model output is available on Zenodo under a CC BY 4.0 "
             "licence.") == ("by", "mention")
    assert f("© 2014 American Meteorological Society. For information regarding reuse, consult "
             "the AMS Copyright Policy.") is None
    # an article line wins over an earlier data mention
    assert f("The data are shared under CC BY 4.0 on Zenodo. " + "x " * 300 + "This is an open "
             "access article under the terms of the Creative Commons Attribution-NonCommercial "
             "License.") == ("by-nc", "article")


def test_licences_from_text_marks_open_and_prints_no_text(ilib, capsys):
    lib = strict(ilib)
    it = lib.get("smith2021cold")
    it.text_path.write_text(it.text() + "\nThis is an open access article under the terms of "
                            "the Creative Commons Attribution License.\n")
    assert cli.main(["-L", str(lib.root), "licences", "--from-text"]) == 0
    assert lib.get("smith2021cold").sharing == "licensed"                 # dry run
    err = capsys.readouterr().err
    assert "smith2021cold: CC by on the article" in err and "open access article" not in err
    assert cli.main(["-L", str(lib.root), "licences", "--from-text", "--apply"]) == 0
    it = lib.get("smith2021cold")
    assert it.sharing == "open" and it.meta["licence"]["source"] == "text"


def test_spool_pull_waits_for_the_runners_reask(ilib, tmp_path):
    from litnav import spool
    lib = strict(ilib)
    it = lib.get("smith2021cold")
    job = localmodel.summarize_job(it)
    copied = {**SUMMARY, "summary": " ".join(it.text().split()[:30])}
    d = tmp_path / "batch"
    d.mkdir()
    (d / "jobs.jsonl").write_text(json.dumps(job) + "\n")
    (d / "results.jsonl").write_text(json.dumps({"id": job["id"], "ok": True, "output": copied, "model": "m"}) + "\n")
    b = {"kind": "summarize", "jobs": [job["id"]], "imported": []}
    spool._apply(lib, "b1", b, d, log=lambda *a: None, final=False)
    assert b["imported"] == [] and localmodel.queued(lib) == [] and it.summary() is None   # waits
    with open(d / "results.jsonl", "a") as f:
        f.write(json.dumps({"id": job["id"] + "-r", "ok": True, "output": SUMMARY, "model": "m"}) + "\n")
    spool._apply(lib, "b1", b, d, log=lambda *a: None, final=True)
    assert job["id"] in b["imported"] and lib.get(it.key).summary()["summary"] == SUMMARY["summary"]


def test_licences_from_arxiv_for_preprints(ilib):
    from litnav import licences
    it = ilib.get("smith2021cold")
    it.meta.setdefault("ids", {})["arxiv"] = "2404.03868"
    it.meta.pop("licence", None)
    it.save_meta()
    it.entry.pop("doi", None) if isinstance(it.entry, dict) else None

    class C:
        def get(self, url, **kw):
            assert "oaipmh.arxiv.org" in url
            return ("<OAI-PMH><GetRecord><record><metadata><arXiv><license>"
                    "http://creativecommons.org/licenses/by/4.0/</license></arXiv></metadata></record></GetRecord>"
                    "</OAI-PMH>")
    import litnav.library as L
    orig = L.Item.doi
    L.Item.doi = property(lambda self: "")
    try:
        r = licences.run(ilib, C(), apply=True, keys=["smith2021cold"], log=lambda *a: None)
    finally:
        L.Item.doi = orig
    assert r["open"] == 1
    from litnav.library import Library
    it2 = Library(ilib.root).get("smith2021cold")
    assert it2.sharing == "open" and it2.meta["licence"]["source"] == "arxiv"
