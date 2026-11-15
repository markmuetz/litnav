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
litnav serve                    # web app on http://127.0.0.1:8765
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
four skills: **researcher** (answers with evidence), **reader** (reading
list, quizzes, recall), **curator** (imports, keys, metadata) and
**explorer** (discovery, the to-fetch list).

```sh
claude plugin marketplace add markmuetz/litnav
claude plugin install litnav@litnav
```

Run Claude Code inside your library (or set `LITNAV_LIBRARY`).

## Sharing levels

Every paper has one: `open`, `licensed` (the default), `private` or
`confidential`. They decide what may be sent to a model or an outside
service. `confidential` items (co-authored drafts, say) are never extracted,
looked up, summarised or shown to any tool.

## Licence

MIT. litnav stores *your* PDFs and the text extracted from them. Keep your
library repo private: pushing the text of licensed papers somewhere public is
redistribution.
