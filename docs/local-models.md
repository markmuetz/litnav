# Licensed papers, local models and batch runs

Most papers in a library are held under a publisher's or university's
licence, and that licence may not allow sending their text to an outside
model (Claude, or any hosted service). litnav keeps such text on your side,
and gives agents what they need without it.

## The setting

`library.toml`:

```toml
[library]
licensed_to_models = false     # licensed papers' text never goes to an outside model
```

With this setting:

| | open | licensed | private | confidential |
|---|---|---|---|---|
| text to an outside model (search passages, sections, Ask, `summarize --via api/claude-code`) | yes | **no** | yes | no |
| metadata (key, title, authors, section titles) to an outside model | yes | yes | yes | no |
| text to **your** model (`local_model`: this machine, or your HPC account) | yes | yes | yes | no |
| model-written summaries and verdicts to an outside model (`send_derived`) | yes | yes | yes | no |

Search still finds licensed papers when an agent searches, but their
passages come back as `[withheld ...]`: the agent knows *where* a match is,
not what it says. To learn whether a licensed paper supports a claim, it asks
`verify` (below).

## Open papers are marked automatically

Many papers are under a Creative Commons licence (JAMES, ACP, GMD, many AGU
and EGU journals). Crossref records the licence:

```sh
litnav licences            # dry run: what would become open
litnav licences --apply    # record each licence in meta.toml; CC BY/BY-SA/CC0 -> open
```

New imports are checked as they are imported. Which CC licences count as
open is yours to set; NC and ND licences are reported, not applied:

```toml
[licences]
open = ["by", "by-sa", "cc0", "publicdomain"]
```

## Public abstracts

An abstract the publisher shows to anyone is metadata, not the paper's text.
`litnav abstracts` fetches it from OpenAlex, Semantic Scholar, Crossref or
arXiv (and from the Springer Nature and Elsevier APIs when
`SPRINGER_API_KEY` or `ELSEVIER_API_KEY` is set) into
`papers/<key>/abstract.json`. It then goes where a summary may: agents see it
for licensed papers too (`get_paper`'s `public_abstract`). To keep it back:

```toml
[abstracts]
to_models = false
```

```sh
litnav abstracts                 # every paper without abstract.json; --recheck to redo
litnav abstracts --doi 10.x/y    # print any paper's abstract
litnav abstracts KEY --from-html page.html   # from a page you saved yourself
```

Publisher sites are never fetched: they refuse automated requests. When no
API has an abstract, open the page in a browser *without* institutional
access (a private window, off campus and off the VPN), save it, and pass it
with `--from-html`. Only the abstract is taken from the page, and only if the
page's title matches the paper's.

## A local model

Any OpenAI-compatible server works: Ollama, llama.cpp's `llama-server`, MLX,
vLLM. On a Mac:

```sh
brew install ollama
OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve &
ollama pull qwen3:30b-a3b                  # ~19 GB; needs ~22 GB of memory with a 32k context
```

Set the context length when starting the server. Ollama otherwise gives a
model only a few thousand tokens and silently drops the rest of a paper.
litnav sends at most `[local_model] max_chars` characters of a paper (default
90,000, about 23k tokens) to fit a 32k context; raise both together for a
model with a longer one. `qwen3:30b-a3b` is a mixture of experts (3B active of
30B): about as fast as a small model, better than the dense 14B. On a 16 GB
machine use `qwen3:8b` or `gemma3:12b` instead.

```text
```

```toml
[local_model]
url = "http://localhost:11434/v1"
model = "qwen3:30b-a3b"
# max_chars = 90000
batch_model = "Qwen/Qwen3-32B"            # what the SLURM template serves on a GPU node
# timeout = 600
# api_key_env = "MY_SERVER_KEY"           # if your server wants a bearer token
```

Then:

```sh
litnav summarize --via local                 # summaries of every paper without one
litnav verify smith2021cold "Geophys. Res. Lett. 48(4), article e2020GL091234"
litnav verify --log                          # past verdicts (derived/verify.jsonl)
```

A verdict is `supported`, `partly_supported`, `contradicted` or `not_found`,
with a confidence, the section and page it rests on, and a sentence of
explanation in the model's words. Shown to an agent, a quotation from a
licensed paper is withheld unless you allow short ones (`[verify] quotes =
true`, at most 25 words). A quotation the model gives that is not in the
text verbatim is dropped. A small model can be wrong: the location is there
so that you can check.

Agents get the same through the MCP tool `verify_claim`. With no local model
running, the check is queued instead.

## Several endpoints: profiles

Besides `[local_model]` (the default, e.g. Ollama on this machine), name other
OpenAI-compatible endpoints in `.litnav/local.toml`, e.g. a bigger model served
with vLLM on a cluster GPU node and reached through an ssh tunnel:

```toml
[local_model.profiles.cluster]
url = "http://127.0.0.1:22310/v1"           # the tunnel's local end
model = "qwen3-235b-a22b-instruct-2507"     # vLLM's --served-model-name
api_key_file = "~/.config/my-llm/token"     # or api_key_env = "MY_LLM_KEY"
context = 65536                             # vLLM's --max-model-len (for OpenCode)
max_chars = 200000                          # paper text per call
timeout = 900
```

A profile's settings are laid over `[local_model]`. Choose one per command with
`litnav -M cluster ...` (or `$LITNAV_MODEL_PROFILE`), or for everything with
`[local_model] profile = "cluster"`; the MCP tools `verify_claim` and
`ask_local_model` take `profile`. `litnav models` lists the endpoints and
whether each answers. Keys are read from a file or an env var, never from
library.toml, and go to OpenCode by env var, not in its config file.

## Questions that need licensed papers: `ask-local`

You keep talking to one agent. When a question needs the text of papers it
may not read, it hands the question to your local model and passes the
answer back:

```sh
litnav ask-local "How do these two papers treat entrainment differently?" \
    -k smith2021cold -k jones2019entrain
litnav ask-local "What does the library say about stochastic triggering?"   # the whole library
```

(MCP: `ask_local_model`.) Two engines:

- `opencode` (the default when [OpenCode](https://opencode.ai) is installed):
  OpenCode runs the local model as an agent with litnav's tools in
  `litnav mcp --local-model` mode, which serves licensed text. It searches,
  reads the sections it needs and answers. litnav writes OpenCode's config
  (`~/.cache/litnav/opencode/opencode.json`): every built-in tool (shell,
  edits, web) is denied, leaving only litnav's; updates and sharing are off;
  and OpenCode runs with all outbound connections sent to a closed local port,
  so only the model server on localhost is reachable. (OpenCode documents no
  switch for its telemetry, so litnav doesn't rely on one.)
- `simple`: one call with the best-matching passages. Faster, and more
  predictable with a small model.

Choose with `--engine`, or `[local_model] engine` in library.toml. The answer
is the local model's, in its words, with `[@key]` citations; any run of 12 or
more words copied from a paper whose text may not go out is cut before the
calling agent sees it. Every question and answer is logged in
`derived/ask-local.jsonl`.

`litnav mcp --local-model` refuses to start unless `ask-local` launched it,
so it can't be wired to Claude Code or another outside model by mistake.

## Batch runs on a bigger model (JASMIN / ORCHID)

Queue work as it comes up, and run it all now and then on a GPU node:

```sh
litnav summarize --via queue                 # or: litnav queue add summarize [KEYS] [--tag T]
litnav queue add verify KEY "claim"          # or: litnav verify KEY "claim" --queue
litnav queue list
litnav queue export ~/litnav-batch-2026-11-22
rsync -a ~/litnav-batch-2026-11-22 jasmin:~/          # however you reach your account
```

On the cluster:

```sh
cd ~/litnav-batch-2026-11-22
cat README.txt
sbatch orchid.slurm                # check its #SBATCH lines against the ORCHID docs first
```

The folder needs only Python 3 and a model server: `run_jobs.py` is the
standard-library runner litnav itself uses, and `orchid.slurm` starts vLLM
on the node and runs the jobs. A run stopped by the time limit resumes when
resubmitted. Back on the laptop:

```sh
rsync -a jasmin:~/litnav-batch-2026-11-22/results.jsonl ~/litnav-batch-2026-11-22/
litnav queue import ~/litnav-batch-2026-11-22
```

### When a node is free only some of the time: the spool

Rather than copying folders by hand, keep a spool on the cluster and let a
worker job take whatever is in it whenever SLURM gives it a node:

```toml
# .litnav/local.toml
[batch_remote]
host = "sci-host.example.org"        # ssh host
spool = "/path/on/the/cluster/spool" # made mode 700; somewhere with room (not a small $HOME)
job_name = "llm-batch"               # the worker's SLURM job name
submit = "sbatch --job-name=llm-batch --export=ALL,LLM_SPOOL=/path/on/the/cluster/spool worker.sbatch"
```

```sh
litnav queue push          # the queued jobs not already out, as one batch; submits the worker if none is queued
litnav queue status        # the worker job, what it is doing, each batch's progress
litnav queue pull          # apply new results (partial ones too); finished batches are deleted on both sides
litnav eval-model push eval/model-claims.jsonl    # an eval as a batch; pull brings its replies back
```

The worker job starts the model server, then runs
`python3 SPOOL/run_spool.py SPOOL --url ... --model ... --concurrency 32 --limit-minutes N`.
It takes batches interrupted by an earlier job first, then the inbox, oldest
first, including batches pushed while it waited. It ends as soon as the
spool is empty (exit 0), or at the time limit with work left (exit 3:
resubmit). Requests run in parallel (`--concurrency`), which is what makes a
vLLM node fast. Push as often as you like; pull whenever you're online.

Results whose paper text changed after the export are dropped, not applied.
The exported folder holds paper text: keep it on your own account and delete
it when the results are back. Confidential papers are never queued or
exported.

## Keeping agents out of the text

litnav's commands apply these rules themselves. Inside an agent's shell
(Claude Code sets `CLAUDECODE=1`; other agents can set `LITNAV_AGENT=1`),
`litnav search` withholds licensed passages the same way the MCP tools do.

An agent with a shell can still read `papers/<key>/text.txt` or run
`pdftotext` on a PDF. The plugin's hook, `litnav guard`, refuses Read, Grep
and Bash calls that touch the text or PDF of a paper that may not go to a
model. `ref.bib`, `meta.toml` and `summary.json` stay readable. It catches
accidents, not a determined agent, and it needs `litnav` on your PATH. If
you don't use the plugin, add it to `~/.claude/settings.json` yourself:

```json
{"hooks": {"PreToolUse": [{"matcher": "Read|Grep|Bash",
  "hooks": [{"type": "command", "command": "litnav --library ~/MyLibrary guard"}]}]}}
```

The guard (like the MCP server) finds your library from `--library`,
`$LITNAV_LIBRARY`, the working directory, or your default:
`litnav use ~/MyLibrary`.
