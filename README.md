# litnav

A local, file-based literature library for researchers and their agents.

Your papers live in a plain directory that is a git repo: one folder per
paper, keyed by citation key, with a normalised `ref.bib`, the extracted text
with its section structure, and your own notes. litnav imports PDFs and
verifies their metadata, keeps the bibliography clean, searches by keyword and
by meaning (locally), maps the citation network, keeps a reading list and
spaced-recall queue, and exposes all of it to Claude (or any MCP client)
through read-only tools and skills. A localhost web app lets you browse,
search and ask questions.

**Status: early development.** Design: [`docs/design.md`](docs/design.md).
Build plan: [`docs/plan.md`](docs/plan.md).

## Install (development)

```sh
uv tool install --editable '.[all]'
litnav init ~/Papers            # or: litnav import-legacy <litman data dir>
```

## Quick start

```sh
cd ~/Papers
# drop PDFs into pdfs/_inbox/ (a subfolder name becomes a tag)
litnav import                   # dry run: shows the keys it would create
litnav import --apply
litnav index                    # sections, chunks, local embeddings
litnav search "cold pools organise convection"
litnav graph                    # citation network from OpenAlex
litnav serve                    # web app: opens http://127.0.0.1:8765/?key=...
```

The web app has Library, Search, Paper, Graph, Tags, Reading and Ask views.
Ask answers questions from your library with links to the passages it used,
through the Anthropic API (`ANTHROPIC_API_KEY`) or, without a key, through
Claude Code if it is installed.

See [`docs/quickstart.md`](docs/quickstart.md) for the full walk-through,
including summaries, discovery and a Zotero export.

## Claude Code

The repo is a Claude Code plugin: an MCP server with read-only tools
(`search_literature`, `get_section`, `get_paper`, `related_papers`, ...) and
five skills: **researcher** (answers with evidence), **synthesis** (topic
notes: syntheses, prior-art checks, reading lists), **reader** (reading
list, quizzes, recall, and `/litnav:discuss <key>` to talk a paper through), **curator** (imports, keys, metadata, projects, drafts) and
**explorer** (discovery, the to-fetch list).

```sh
claude plugin marketplace add markmuetz/litnav
claude plugin install litnav@litnav
```

Run Claude Code inside your library, set `LITNAV_LIBRARY`, or make it your default with
`litnav use <dir>`.

## Sharing levels

Every paper has one: `open`, `licensed` (the default), `private` or
`confidential`. They decide what may be sent to a model or an outside
service. `confidential` items (co-authored drafts, say) are never extracted,
looked up, summarised or shown to any tool.

If your papers' licences don't allow sending their text to an outside model,
set `licensed_to_models = false`: summaries and claim checks then run on a
model you run yourself (Ollama on your laptop, or a batch on a GPU cluster),
`litnav licences` marks Creative Commons papers open, a Claude Code hook
keeps agents out of the rest, and `litnav ask-local` lets your agent hand a
question about those papers to your local model. See [`docs/local-models.md`](docs/local-models.md).
Commands for building and filling a claim graph's schema (G0, the model registry, topics, topic templates,
saturation): [`docs/passes.md`](docs/passes.md).

## Licence

MIT. litnav stores *your* PDFs and the text extracted from them. Keep your
library repo private: pushing the text of licensed papers somewhere public is
redistribution.

How it works inside: [docs/architecture](docs/architecture/README.md) (six documents with diagrams, also as PDFs).
