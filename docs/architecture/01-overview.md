---
title: "litnav architecture 1: Overview"
subtitle: "The map: what the parts are, how they connect, and what you do with them"
date: "2026-11-15 (v0.1.0)"
---

# How to read these documents

| # | Document | What it answers |
|---|---|---|
| 1 | **Overview** (this one) | What are the parts, how do they connect, what do you ask Claude to do with them? |
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
files. Everything else is a way in or out of that pair: a **CLI**, run
mostly by Claude Code on your behalf, a **web app** on localhost for
browsing, an **MCP server** that gives Claude Code read-only tools, and a few **workflows** that write to the files
(import, summaries, reading lists, discovery). Every path that could send
something off the machine asks one table in `sharing.py` first.

# The map

![High-level map. Arrows point from caller to callee. The core never calls the interfaces.](diagrams/system-map.pdf){width=100%}

Reading the map from top to bottom:

1. **Who.** Mostly Claude Code, working for you with the litnav plugin
   (five skills and `/litnav:discuss`), and you in a browser. You can run
   the CLI yourself, but the usual way in is to ask Claude.
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

**You talk to Claude; Claude runs litnav.** You rarely type a litnav command.
You tell Claude Code, started in the library folder, what you want; it reads
the library through the MCP tools, makes changes with the CLI, and follows the
plugin's skills and the library's `CLAUDE.md`. You decide (what to read,
whether a match is right, what is confidential); Claude does the steps. The
web app is the one place you work directly, for browsing.

| You tell Claude... | Claude... | Flow |
|---|---|---|
| "I've downloaded two papers on cold pools" | finds them in Downloads, checks none is a draft or a manuscript you are refereeing, imports them with verified metadata, indexes and summarises them, files them in a project or on the reading list if you say so, and reports the new keys | **3.1**, **3.2**, **3.4** |
| "What does my library say about X?" | searches by keyword and by meaning, reads the matching sections and your notes, and answers with every claim cited to a passage (`[@key]` + chunk ID) | **3.2**, **3.3** |
| "Write up what's known about X" / "Has anyone done Y?" | checks your topic notes, searches the library, then outside sources by metadata only, and writes or updates `topics/<slug>.md` with a short answer and a search record | **3.2**, **3.5** |
| "What am I missing on X?" / "Get me these papers" | lists works your library cites but lacks, or recommendations, adds the ones you pick to the to-fetch list, and opens their DOIs in your browser for you to download | **3.5** |
| "Add X to my reading list, skim" / "I've finished X" | updates the reading list; when you finish, records the depth and makes a recall card; starts a notes file from the template if you ask | **3.6** |
| "What's due?" / "Let's discuss X" | gives one summary of recall cards and reading due; rates cards with you; in a discussion, asks you to explain the paper and logs what was solid and what was shaky | **3.6** |
| "Add these to ALOFT, to read" / "ALOFT papers on X" | updates the project's papers, status and tags; searches within a project | **3.2** |
| "Make a .bib for this paper" | exports BibTeX for the keys cited, and checks every `[@key]` resolves | |
| "Here's the new version of the X draft" | files it as the current version, moves the old one to `_variants/`, logs it; never opens it | **5** |
| "This key/bib is wrong" / "these are the same paper" | fixes metadata, renames keys (the old key stays as an alias), merges duplicates with your say on which survives | **3.1** |
| "Open the library" | starts the web app and gives you its link, for browsing papers, the citation graph and Ask yourself | **4.3** |

What stays with you: downloading PDFs (Claude only opens DOIs), deciding
what is read and how well you remember it, writing your notes, and marking
anything confidential that the library cannot recognise on its own (a note
written for a review: front matter `sharing: confidential`).

For one paper followed end to end, through every flow and the files each
step writes, see "A day in the life of one paper" at the end of document 3.
