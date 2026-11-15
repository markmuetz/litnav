---
title: "litnav architecture 6: Module reference"
subtitle: "Every source file, what it does, and how they depend on each other"
date: "2026-11-15 (v0.1.0)"
---

# The import graph

Generated from the code (`docs/architecture/diagrams/gen_modules.py`). Green: interfaces; grey: workflows and tools; blue: index and search; purple: network; yellow: the library model. Arrows point from importer to imported. `commands/*` (the CLI layer) is left out: each command module imports the core module of the same name.

![Internal imports between modules.](diagrams/modules.pdf){width=100%}

Read it right to left for the foundations: `util`, `sharing`, `keys`, `config`, `bib`, `meta` and `library` depend on almost nothing; `net` depends only on `sharing`; everything that talks to the outside goes through `net`.

# Modules

| Module | Lines | Role | Public names |
|---|---:|---|---|
| `ask` | 285 | Ask: Anthropic API loop or claude -p over the MCP server; events. | `backend_status`, `stream_answer` |
| `bib` | 324 | BibTeX parse/write and house-style normalisation (authority files). | `Entry`, `unwrap`, `parse`, `parse_many`, `Authority`, `redelimit`, `tidy_name`, `fix_authors`, `fix_journal` ... |
| `chunks` | 201 | Section-aware chunking with stable chunk IDs. | `Chunk`, `windows`, `chunk`, `header` |
| `cli` | 51 | Entry point: builds the argparse tree from commands/*. | `build_parser`, `main` |
| `config` | 129 | library.toml defaults, loading, the local.toml overlay. | `LibraryNotFound`, `find_root`, `load`, `pdf_dir` |
| `derived` | 414 | Summaries and themes: selection, outbound gate, API batches, Claude Code. | `outbound`, `text_for`, `is_current`, `select`, `provenance`, `valid`, `write_summary`, `state_path`, `api_submit` ... |
| `discovery` | 224 | Holdings, candidates, Semantic Scholar explore, to-fetch list. | `norm_doi`, `fold_title`, `Holdings`, `tofetch_path`, `load`, `save`, `add`, `set_status`, `prune` ... |
| `embed` | 147 | Local embeddings (fastembed), vector store, matrices. | `available`, `vhash`, `Embedder`, `chunk_text`, `paper_text`, `update`, `matrix`, `allowed_mask` |
| `evaluate` | 180 | Search evaluation: recall@10, MRR, compare runs. | `questions_path`, `load`, `fold`, `score_one`, `run`, `summarise`, `report`, `sign_test`, `compare` ... |
| `extract` | 131 | pdftotext and OCR; text.txt for items. | `NoBackend`, `backends`, `pdf_text`, `own_text`, `has_text_layer`, `ocr_available`, `ocr`, `head`, `normalise_text` ... |
| `graph` | 84 | Citation graph and candidates from OpenAlex. | `short`, `build` |
| `identify` | 149 | The identification ladder and title verification. | `Proposal`, `clean_doi`, `ams_doi`, `marker_for`, `verify`, `identify` |
| `importer` | 242 | Import plan/apply, duplicate catalogue, inbox handling. | `norm_title`, `Catalogue`, `inbox_files`, `plan`, `plan_path`, `write_plan`, `read_plan`, `apply` |
| `importers.bibtex` | 109 | Import from a Zotero/JabRef/Mendeley .bib with file fields. | `file_paths`, `pdf_for`, `plan`, `apply` |
| `importers.legacy` | 114 | Convert a litman data directory. | `read_tags`, `convert` |
| `index` | 186 | The SQLite index: schema, incremental update, signatures. | `db_path`, `connect`, `writer`, `signature`, `index_item`, `update`, `section_text`, `to_json` |
| `init` | 132 | litnav init: create a library, ask the licensed-text question. | `in_synced_folder`, `init`, `ask_licensed_to_models` |
| `keys` | 195 | Citation-key derivation, folding, stop words, collision handling. | `fold`, `plain`, `words`, `fold_text`, `content_words`, `plausible_year`, `part_number`, `surname`, `derive` ... |
| `library` | 264 | Item and Library: the file model, resolve/aliases, items(purpose). | `KeyError_`, `Item`, `Library` |
| `mcp_server` | 51 | FastMCP stdio server over tools. | `build`, `main` |
| `meta` | 47 | meta.toml read/write (stable field order, empty tables dropped). | `empty`, `read`, `write`, `today` |
| `net` | 121 | The only HTTP client: sharing check, cache, rate limit, retries. | `Refused`, `NetError`, `Client`, `open_in_browser` |
| `projects` | 41 | Project membership lists. | `path`, `load`, `save`, `names`, `project_keys` |
| `quizzes` | 72 | Quiz and discussion logs. | `path`, `sessions`, `listing`, `start` |
| `reading` | 161 | Reading list and Leitner recall scheduling. | `list_path`, `recall_path`, `load_list`, `load_cards`, `add_to_list`, `mark_done`, `next_working_day`, `off_weekend`, `add_months` ... |
| `refs` | 103 | [@key] parsing, backlinks, check/convert refs. | `Ref`, `find`, `md_files`, `check`, `backlinks`, `rewrite`, `convert_backticks` |
| `rename` | 86 | Rename a key everywhere, keeping an alias. | `plan`, `apply` |
| `search` | 239 | Keyword, semantic and hybrid search with filters and purpose. | `Filters`, `parse_query`, `fts_query`, `keyword`, `semantic`, `hybrid`, `search`, `papers`, `similar` |
| `sections` | 245 | Section detection in extracted text. | `Section`, `Parsed`, `strip_running_heads`, `parse` |
| `sharing` | 49 | Sharing levels, purposes, the allow() table. | `check_level`, `allow`, `effective` |
| `sources.arxiv` | 55 | arXiv API: metadata by id. | `find_id`, `family`, `lookup`, `to_bibtex` |
| `sources.crossref` | 110 | Crossref works API: by DOI, title query, BibTeX. | `work`, `query`, `issue_year`, `title`, `clean_title`, `first_family`, `tex`, `to_bibtex`, `key_for` |
| `sources.openalex` | 46 | OpenAlex works: by DOI, reference lists. | `by_doi`, `by_ids`, `cc_licence` |
| `tools` | 155 | Read-only tools shared by MCP and Ask. | `Tools` |
| `util` | 38 | Atomic writes and small helpers. | `atomic_write`, `sha256_file`, `short_hash` |
| `web.app` | 351 | Starlette web app: guard, API routes, git commits of writes. | `Git`, `create_app`, `serve` |

# The CLI layer (`commands/`)

| File | Commands |
|---|---|
| `commands/core.py` | init, list, show, tag, share, bib, check, normalise. |
| `commands/derived_cmds.py` | summarize, themes. |
| `commands/discovery_cmds.py` | candidates, explore, to-fetch. |
| `commands/importing.py` | import, import-bib, import-legacy, fetch. |
| `commands/reading_cmds.py` | read (reading list), due, recall, quiz. |
| `commands/refs.py` | check-refs, convert-refs, backlinks, rename, graph. |
| `commands/searching.py` | search, similar, eval. |
| `commands/text.py` | extract, index, embed, doctor. |
| `commands/web_cmds.py` | mcp, serve. |

# Web front end (`web/static/`)

| File | Role |
|---|---|
| `index.html` | one page; the session token is injected as `__TOKEN__` |
| `app.js` | views (Library, Search, Paper, Tags, Reading, Discovery, Ask), hash routing, safe Markdown rendering, SSE for Ask |
| `graph.js` | force-directed citation graph on a canvas; colour by decade/tag; candidate rings |
| `style.css` | dark theme |

# Tests

`tests/` runs with `pytest` (109 tests, ~3 s): library and keys, bib normalisation, import and identification with recorded API responses, sections and chunks, index and search, the sharing gates (net refusal, outbound, MCP tools, search filters), derived routes with a fake `claude`, the web app's guard and endpoints, reading and recall dates, discovery, quizzes. CI runs them on Ubuntu, macOS and Windows with Python 3.11-3.13.
