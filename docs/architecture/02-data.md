---
title: "litnav architecture 2: Data and storage"
subtitle: "Where everything lives, what is in it, who writes it, and what comes from outside"
date: "2026-11-15 (v0.1.0)"
---

# Three places on disk

![Library layout. Yellow is tracked in the library's git repo; blue is derived (rebuildable); orange is the PDF store.](diagrams/storage.pdf){width=100%}

| Place | Default | Tracked? | Lose it and... |
|---|---|---|---|
| **Library root** | the directory holding `library.toml` (e.g. `~/Papers`) | yes, git | you lose your data: back it up (push it to a *private* remote) |
| **PDF store** `pdf_dir` | `<root>/pdfs/`, or an absolute path such as a synced folder | no (gitignored) | `text.txt` survives, so search still works; the PDFs themselves are gone |
| **State dir** `.litnav/` (or `index_dir`) | `<root>/.litnav/` | no (gitignored) | nothing: `litnav index --rebuild`, `litnav graph` recreate it |

The library is located by `--library DIR`, else `$LITNAV_LIBRARY`, else the
nearest parent directory with a `library.toml` (`Library.find`).
`.litnav/local.toml` overlays `library.toml` for one machine (e.g. a
different `pdf_dir` on a second computer).

# Per-paper files: `papers/<key>/`

| File | Written by | Contents | Notes |
|---|---|---|---|
| `ref.bib` | import, `import-bib`, `normalise`, `rename` | One BibTeX entry, house style: field order `title, author, ... , url, doi`, 4-space indent, `{{Acronym}}` protection | `bib.normalise_entry` is idempotent; authority files in `curation/` canonicalise author and journal names |
| `meta.toml` | import, `tag`, `share`, `rename`, `graph` (OpenAlex id, CC licence) | `sharing`, `tags`, `aliases`, `added`, `[ids]` (openalex, arxiv...), `[relations]` (`preprint_of`, `corrigendum_of`, `draft_of`, `supplement_of`, `part_of`), `[files]` (`pdf_sha256`, `text_source`, `pdf_path`, `licence`) | Empty tables are dropped so diffs stay small. An item with only `ref.bib` is valid. |
| `text.txt` | import, `extract` | Raw extracted text (`pdftotext`, or OCR) | Never written for confidential items. Sections are found at index time, not stored. |
| `summary.json` | `summarize` | `topic`, `summary`, `keywords` plus `provenance` (model, route, text sha, truncated) | Stable JSON: re-running with the same output changes nothing. A changed text sha marks it stale (`--stale`). |

**Keys.** `<surname><year><firstcontentword>[part][_marker]`, accents
folded, leading function words skipped (`keys.py`). Markers:
`_preprint`, `_draft`, `_corrigendum`, `_supplement`, ... A key must be a
safe filename and unique case-insensitively, including aliases.

# Your own files

| Path | Format | Written by | Read by |
|---|---|---|---|
| `notes/<key>.md` | Markdown with `[@key]` citations | you | `get_note` tool, backlinks, `check-refs`, web Paper view |
| `concepts/`, `topics/`, `glossary.md` | Markdown | you | backlinks; skills |
| `reading/list.toml` | `[[paper]]` key, depth, added, status, done, note | `read add/done`, web Reading tab | `read list`, `due`, `reading_due` tool |
| `reading/recall.toml` | `[[card]]` id, box, last_reviewed, next_due, prompt | `read done`, `recall` | `due`, `reading_due` |
| `quizzes/<key>.md` | dated sections: quiz table or discussion bullets | `quiz start` + you/Claude | `quiz show`, `/litnav:discuss` |
| `projects/<P>/papers.toml` | `[[paper]]` key, status, tags, added, note | import from `_inbox/<P>/`, you | `--project` filters, `bib --project` |
| `discovery/to-fetch.tsv` | doi, title, first_author, year, source, added, status, note | `candidates --add`, `explore --add`, `to-fetch`, web Discovery | `to-fetch list/open`; pruned on import |
| `derived/themes.md` | Markdown report with `[@key]` | `themes` | you; backlinks |
| `eval/questions.toml` | questions with expected keys/passages | you | `eval` |
| `curation/*.tsv` | authority files (author, journal names) | you | `bib.normalise` |

Every `[@key]` in any of these directories (`refs.NOTE_DIRS`) is a
backlink: `refs.backlinks()` scans the notes when asked, so a paper's page
shows which notes cite it; `rename` rewrites them.

# The derived index: `.litnav/index.sqlite`

![Index tables and how they are filled.](diagrams/index-schema.pdf){width=100%}

- **One row per item** in `items` (catalogue fields, sharing, `sig`), with
  `items_fts` (FTS5: title, authors, tags, summary) for paper-level search.
- **Sections** are detected from `text.txt` at index time (`sections.py`,
  version-stamped). **Chunks** (`chunks.py`) carry the section, offsets
  into `text.txt`, the verbatim text, a header (`title | section`) and the
  item's **sharing level**, so search can filter at chunk level.
- **Chunk IDs** are `key#section-slug#hash8`, the hash over the chunk's
  normalised text: re-chunking keeps the IDs of unchanged text, so IDs quoted
  in notes or answers stay valid.
- **Vectors** are keyed by `hash(model, embedded text)`, not by chunk ID.
  Duplicate texts share a vector; changing the model or the text creates a
  new one; nothing is embedded twice.
- **Incremental update.** `index.update()` compares each item's signature
  (sizes and mtimes of its four files plus the chunker and section-parser versions) and re-indexes
  only changed items; removed items are deleted. `embed.update()` then embeds
  any chunk or paper text without a vector. A chunk's vector hash is set when it is
  indexed, so reindexing an item (e.g. after a tag change) keeps it in semantic
  search.
- **Concurrency.** WAL mode; writers use `index.writer()`. Long-lived readers
  (web app, MCP server) hold one read-only connection through
  `index.Reader` (reopened if the file is deleted and recreated by hand).
  `litnav index --rebuild` works **in place**: it drops and recreates the
  tables in the same file, so open readers stay valid, and keeps the stored
  vectors (keyed by a hash of their text), so nothing unchanged is embedded
  again.
- **Generation.** A write that changes which passages or papers have
  vectors bumps `state.gen`. The in-memory matrix of vectors
  (`embed.matrix`) is cached per index file and rebuilt only when the
  generation or the vector count changes; the **sharing levels are read
  fresh for every query**, so a sharing change reaches semantic search at
  once without rebuilding the matrix.
- **Sharing is stored twice**: in `meta.toml` (the truth) and copied onto
  `items`/`chunks` for fast filtering. `litnav share` and the web app reindex
  the item at once; the model-facing tools also re-check each result against
  `meta.toml`, so a hand edit is safe before the next `litnav index`.

Other derived files in `.litnav/`:

| File | From | Contents |
|---|---|---|
| `graph.json` | `litnav graph` | nodes (library papers), links (internal citations), candidates (outside works with `cited_by_library` and `citers`) |
| `cache/http/xx/<sha>.json` | `net.Client` | cached API responses (metadata only); `cache=False` for recommendations |
| `import-plan.tsv` | `litnav import` (dry run) | the plan `--from-plan` applies |
| `summarize_batch.json` | `summarize --via api` | the open Batch API job, for resumption |
| `local.toml` | you | per-machine config overlay |

# Data sources: what comes in from outside

| Source | Module | What is sent | What comes back | Used for |
|---|---|---|---|---|
| **Crossref** `api.crossref.org/works` | `sources/crossref.py` | a DOI, or a title query (opening lines of a PDF) | BibTeX and JSON metadata | import, `find-doi`-style lookups |
| **arXiv** `export.arxiv.org/api/query` | `sources/arxiv.py` | an arXiv id | Atom metadata, DOI if published | import of preprints |
| **OpenAlex** `api.openalex.org/works` | `sources/openalex.py` | a DOI | work id, reference list, licence | `graph`, candidates |
| **Semantic Scholar** recommendations | `discovery.py` | the seed papers' DOIs | recommended papers | `explore` |
| **Anthropic API** | `derived.py`, `ask.py` | paper text (summaries), summaries (themes), questions and tool results (Ask) | summaries, themes, answers | only if sharing allows |
| **`claude` CLI** (your login) | `derived.py`, `ask.py` | as above, via a subprocess | as above | default route |
| **Local models** | `embed.py` (fastembed `BAAI/bge-small-en-v1.5`) | nothing leaves the machine | 384-d vectors | semantic search |
| **Local tools** | `extract.py` | nothing leaves the machine | text from `pdftotext`; OCR from macOS Vision or tesseract | `text.txt` |

All HTTP goes through `net.Client`: per-host rate limiting, retries with
backoff on 429/5xx, a `User-Agent` with your `mailto` (polite pools), and a
**sharing check before anything is sent** (document 5). The Anthropic SDK and
the `claude` subprocess are the two other exits, both behind
`derived.outbound()` or the read-only `Tools` purpose.

# Output locations: who writes what

| Command / action | Writes | Commits to git? |
|---|---|---|
| `litnav init` | `library.toml`, `.gitignore`, `.gitattributes`, `README.md`, `eval/questions.toml`, folders; `git init` | initial commit |
| `import --apply` | `papers/<key>/*`, `<pdf_dir>/<key>.pdf`, `_variants/`, `projects/<P>/`, `discovery/to-fetch.tsv` | no: you commit |
| `extract` | `papers/<key>/text.txt`, `meta.toml` (`text_source`) | no |
| `index` | `.litnav/index.sqlite` | n/a (untracked) |
| `summarize` | `papers/<key>/summary.json`, then the index | no |
| `themes` | `derived/themes.md` | no |
| `graph` | `.litnav/graph.json`, `edges` table, `meta.toml` (`[ids] openalex`, CC licence) | no |
| `tag`, `share`, `rename`, `normalise`, `convert-refs` | `meta.toml` / `ref.bib` / notes | no |
| `read`, `recall`, `quiz start` | `reading/*.toml`, `quizzes/<key>.md` | no |
| `to-fetch`, `candidates --add`, `explore --add` | `discovery/to-fetch.tsv` | no |
| `eval --save` | `eval/runs/<date>-<commit>.json` | no |
| **web app** writes (tags, sharing, reading, to-fetch) | the same files | **yes: one commit per write**, only the touched paths, skipped during a merge or rebase |

The CLI leaves committing to you (so a batch of changes is one commit); the
web app commits each click because there is no natural moment to ask.
