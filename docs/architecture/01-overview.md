---
title: "litnav architecture 1: Overview"
subtitle: "The map: what the parts are, how they connect, and what you do with them"
date: "2026-11-15 (v0.1.0)"
---

# How to read these documents

| # | Document | What it answers |
|---|---|---|
| 1 | **Overview** (this one) | What are the parts, how do they connect, what are the common jobs? |
| 2 | **Data and storage** | Which files exist, what is in them, who writes them, which outside services are called |
| 3 | **Data flows** | Step by step: import, index and search, Ask, summaries and themes, citation graph and discovery, reading and recall |
| 4 | **Interfaces** | The CLI, the MCP server and its tools, the web app's HTTP API, the Claude Code plugin |
| 5 | **Privacy and sharing** | What can leave your machine, and the gates that enforce it |
| 6 | **Module reference** | Every source file: its job, what it depends on, and the import graph |

Colours are consistent across the diagrams: **yellow** = files in your library
(tracked in git), **orange** = PDFs, **blue** = derived and rebuildable,
**green** = interfaces, **purple** = metadata services, **red** = anything that
receives text (models).

# The one-paragraph version

litnav is a **folder of files** (one directory per paper, plus your notes and
reading lists) kept in git, and a **derived SQLite index** built from those
files. Everything else is a way in or out of that pair: a **CLI** for you, a
**web app** on localhost for browsing, an **MCP server** that gives Claude
Code read-only tools, and a few **workflows** that write to the files
(import, summaries, reading lists, discovery). Every path that could send
something off the machine asks one table in `sharing.py` first.

# The map

![High-level map. Arrows point from caller to callee. The core never calls the interfaces.](diagrams/system-map.pdf){width=100%}

Reading the map from top to bottom:

1. **Who.** You, at a terminal or in a browser, and Claude Code with the
   litnav plugin installed (four skills and `/litnav:discuss`).
2. **Interfaces.** Three thin front ends. The CLI does everything; the web app
   reads and does a few small writes (tags, sharing, reading list, to-fetch);
   the MCP server is read-only.
3. **Core.** Two kinds of code: *workflows* that change the library (import,
   summarize, graph, discovery, reading, rename) and *read-only tools* (search,
   read a section, get a paper, notes). Both sit on the index/search layer and
   on the library model, which owns the files and the sharing rules.
4. **On disk.** The library repo (the truth), the PDF store (often a synced
   folder such as Dropbox), and `.litnav/` (index, graph, caches: delete it and
   `litnav index` rebuilds it).
5. **Outside.** Metadata services (Crossref, arXiv, OpenAlex, Semantic
   Scholar) via `net.py`, and models (the Anthropic API, or your own `claude`
   CLI login) for Ask, summaries and themes.

# Layers

![The layers. Each depends only on those below it.](diagrams/layers.pdf){width=95%}

Two rules keep the layers honest:

- **Files are the truth; the index is a cache.** Nothing is only in
  SQLite. `index.sqlite` can be deleted at any time; `litnav index --rebuild`
  reproduces it, and the embeddings are keyed by a hash of their text so
  unchanged passages are not re-embedded.
- **One gate for anything outbound.** `sharing.allow(level, purpose)` is the
  only place that decides; `net.Client` (the only HTTP client),
  `derived.outbound()` (model-bound text) and `Library.items(purpose)` all
  call it. Detail in document 5.

# Design principles

| Principle | What it means in the code |
|---|---|
| Plain files, git history | `ref.bib`, `meta.toml`, `text.txt`, `summary.json`, Markdown notes; every write is a small diff. The web app commits each write. |
| Dry run, then apply | `import` writes a plan (`.litnav/import-plan.tsv`) you review; `--apply` performs it; `--from-plan` applies it without re-querying. `rename` and `convert-refs` also plan first. |
| Verify, don't trust | An import proposal is accepted only if the resolved title's distinctive words are in the PDF; title-query guesses need a higher bar. |
| Never delete PDFs | Duplicates go to `_variants/`; unidentified files stay in `_inbox/`. |
| Stable references | Citation keys never change silently (rename keeps an alias); passages have stable IDs `key#section#hash8`. |
| Evidence with every answer | Search returns verbatim passages with chunk IDs; Ask and the skills must cite `[@key]` plus a chunk ID. |
| Local first | Embeddings run locally (fastembed/ONNX). Models are optional: everything except Ask, summaries and themes works without one. |
| No automated PDF fetching | Discovery opens DOIs in your browser; you save the PDF. |

# Common use cases

Each row is a job you do; the right-hand column names the flow in document 3.

| You want to... | You run | What happens (flow) |
|---|---|---|
| Add new papers | save PDFs to `<pdf_dir>/_inbox/`, then `litnav import`, check, `litnav import --apply` | Identify, verify, de-duplicate, create items, prune to-fetch (**3.1**) |
| Make them searchable | `litnav index` (and `litnav summarize --via claude-code` if you want summaries) | Sections, chunks, FTS, embeddings (**3.2**); summaries (**3.4**) |
| Find where something is said | `litnav search '"TWP-ICE" downdrafts'`, or the web Search tab | Hybrid keyword + semantic search over passages (**3.2**) |
| Ask a question of the library | web Ask tab, or ask Claude Code (researcher skill) | Tool-use loop over read-only tools, cited answer (**3.3**) |
| Read a paper's part | web Paper view, or `get_section` via MCP | Section text from the index; PDF from `/pdf/<key>` |
| See how papers connect | `litnav graph`, then the web Graph tab | OpenAlex references -> internal edges + outside candidates (**3.5**) |
| Find what you are missing | `litnav candidates`, `litnav explore <keys>`, web Discovery tab | Candidates and recommendations -> `to-fetch.tsv` -> browser (**3.5**) |
| Plan and track reading | `litnav read add <key> --depth read`, `litnav read done <key> --depth read` | Reading list and recall cards (**3.6**) |
| Keep what you read | `litnav due`, `litnav recall <id> easy`, `/litnav:discuss <key>` | Leitner scheduling, quiz and discussion logs (**3.6**) |
| Write with citations | `litnav bib --project P --out refs.bib`; `[@key]` in notes | BibTeX export; backlinks from notes |
| Keep a draft private | put it in `_inbox/_confidential/` or `litnav share <key> confidential` | Never read, looked up, or sent (**5**) |

# A day in the life of one paper

1. You download `JAS-D-23-0123.pdf` into `_inbox/UPFLO/`.
2. `litnav import` finds DOI `10.1175/...` on page 1, asks Crossref for the
   record, checks the title's words are in the PDF, sees no existing item with
   that hash, DOI or title, and proposes key `smith2024cold`, tag `UPFLO`.
3. `--apply` writes `papers/smith2024cold/{ref.bib, meta.toml, text.txt}`,
   moves the PDF to `<pdf_dir>/smith2024cold.pdf`, and removes the DOI from
   the to-fetch list.
4. `litnav index` splits the text into sections and ~300-word chunks
   (`smith2024cold#3-2#9f1c04ab`), adds them to the full-text index, and embeds
   them locally.
5. `litnav summarize --via claude-code` checks the paper's sharing level allows
   sending text to a model, sends the text to Haiku through your Claude Code
   login, and writes `summary.json`; re-indexing makes the summary searchable
   and improves the paper's vector.
6. Claude Code, asked about cold pools, calls `search_literature` over MCP,
   gets this paper's passage with its chunk ID, reads the section with
   `get_section`, and cites `[@smith2024cold]` with the chunk ID.
7. You add it to the reading list, read it, write `notes/smith2024cold.md`,
   mark it done, and it becomes a recall card. A week later
   `/litnav:discuss smith2024cold` asks you to explain it, and logs the session.
