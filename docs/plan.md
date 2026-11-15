# litnav implementation plan

Companion to `docs/design.md` (the *what* and *why*); this is the *how* and
*in what order*. Section numbers like § 6 refer to the design doc. Each
milestone ends with its tests passing and a commit; a milestone is not done
until its "done when" line is true.

## Stack

| concern | choice | why |
|---|---|---|
| language | Python ≥ 3.11 | `tomllib` in the standard library; scientists' language |
| packaging | `pyproject.toml`, hatchling, `uv` | `uv tool install litnav` for users |
| CLI | `argparse`, one module per command group | no dependency; subcommands |
| TOML write | `tomli-w` | tiny, MIT |
| HTTP | `urllib` from the standard library, one polite client | Crossref, OpenAlex, arXiv, S2 are simple JSON GETs |
| PDF text | `pdftotext` if installed, else `pypdfium2` | pdftotext's layout mode is best on two-column papers; pypdfium2 has wheels everywhere. Not PyMuPDF (AGPL). |
| OCR | `ocrmypdf`/`tesseract` if installed; Apple Vision on macOS | optional, detected at run time |
| index | SQLite with FTS5 (standard library) | keyword search, catalogue, edges |
| embeddings | `fastembed` (optional extra `[search]`), NumPy | local, no PyTorch |
| web | Starlette + uvicorn (extra `[web]`); plain JS, no build step | small; the graph page is already plain JS |
| MCP | official `mcp` Python SDK (extra `[mcp]`), stdio | what Claude Code speaks |
| Ask | `anthropic` SDK (extra `[ask]`) or the `claude` CLI | § 16a |
| tests | `pytest`, fixtures built in `tmp_path`, network mocked | fast, offline |

Extras keep the core install small: `litnav` alone does library, import,
bib, keys, keyword search and reading; `litnav[all]` adds embeddings, web,
MCP and Ask.

## Layout

```
pyproject.toml  LICENSE  README.md
src/litnav/
  cli.py                 entry point; subcommand registry
  config.py              library.toml + .litnav/local.toml; find the library root
  library.py             Library, Item; items(purpose=...) (§ 6)
  sharing.py             levels, purposes, the allow() table
  meta.py                meta.toml read/write
  keys.py                derive, validate, fold accents, stop words, collisions
  bib.py                 parse, normalise (port of normalise_bib), export
  authority.py           optional author/journal authority files
  net.py                 polite HTTP client: User-Agent, mailto, retries, cache
  sources/crossref.py  arxiv.py  openalex.py  s2.py
  identify.py            DOI → arXiv → title ladder, verification (§ 7)
  importer.py            plan / apply / from-plan; dedupe by DOI, hash, title
  importers/bibtex.py    import from a .bib with file fields (Zotero, JabRef)
  importers/legacy.py    convert a litman data directory
  extract.py             PDF → text, OCR fallback
  sections.py            heading detection (at index time)
  chunks.py              section-aware chunks with stable IDs
  index.py               SQLite schema, incremental build, FTS5
  embed.py               fastembed wrapper, vectors file
  search.py              keyword, semantic, hybrid (RRF), filters, similar
  graph.py               OpenAlex references → edges; network JSON; candidates
  refs.py                [@key] parse, check, rewrite, backlinks
  rename.py              rename + aliases + reference rewrite
  reading.py             reading list, depths, triage
  recall.py              Leitner boxes, working days, due
  derived.py             summaries, themes (Claude), provenance
  ask.py                 Ask back ends: api, claude-code
  mcp_server.py          read-only tool group; write tool group behind a flag
  web/app.py             Starlette routes, JSON API, /pdf/<key>
  web/static/            index.html, app.js, graph.js, style.css
skills/curator/SKILL.md  researcher/  reader/  explorer/
.claude-plugin/plugin.json   .mcp.json
tests/                   one test module per source module
docs/design.md  docs/plan.md  docs/quickstart.md
```

## Milestones

### M0. Skeleton

- repo, MIT licence, README, `pyproject.toml`, `uv` dev setup, `pytest`.
- `litnav --version`, `litnav init <dir>`: creates the layout of § 3,
  `library.toml` with commented defaults, `.gitignore` (pdfs, `.litnav/`),
  library README with the privacy warning, `git init` + first commit.
- `config.find_root()`: `--library`, `$LITNAV_LIBRARY`, or walk up from cwd.

Done when: `uv tool install .` then `litnav init /tmp/x` produces a valid
library, and the test suite runs.

### M1. Core model: keys, bib, meta, sharing

- `keys.py`: port `derive_keys` (stop words, accent folding, plausible year)
  and add parts, markers, corporate authors, collisions (append the next
  content word), case-folded uniqueness, `key_style = keep`.
- `bib.py`: port `normalise_bib` verbatim in behaviour (five passes, the value
  survival check); parse to a small `Entry` (type, key, ordered fields,
  preamble); `export(keys)`.
- `authority.py`: author and journal authority files, optional, in the
  library's `curation/`; absent files mean the mechanical layer only.
- `meta.py`, `library.py`: `Item` (key, path, bib, meta, pdf path, text path,
  summary), `Library.items(purpose)`, `Library.get(key_or_alias)`,
  `create_item`, `add_tag`, `set_sharing`.
- `sharing.py`: the table of § 6; `allow(item, purpose)`.
- CLI: `litnav list [--tag T] [--format keys|table|json]`, `show <key>`,
  `tag add|rm`, `share <key> <level>`, `bib [keys|--tag|--project]`,
  `check` (convention, plausible year, missing DOI, bib unparseable).

Tests (§ 14): key derivation cases from the old corpus (issue year; `toward`
kept; `what is the role` → `role`; accents; `grl28889` rejected; IPCC);
bib normalisation golden files (`"{X}"` → `{{X}}`, all-caps names, bare
months, idempotence, value survival); sharing table.

Done when: these tests pass and `litnav bib` round-trips 50 real `ref.bib`
files byte-identically after one normalisation pass.

### M2. Import

- `net.py` + `sources/`: Crossref works and query, arXiv API, OpenAlex works,
  S2 paper lookup. One User-Agent (`litnav/<version>`, mailto only if the user
  configures one), retries with backoff, an on-disk JSON cache in `.litnav/`.
- `extract.py` (minimal here): first pages of text for identification.
- `identify.py`: the ladder; verification ratio 0.6 for identifiers, 0.75 for
  title queries; arXiv → `_preprint`.
- `importer.py`: scan `_inbox/` (subdirectory = tag), dedupe by DOI, SHA-256
  and normalised title, plan to `.litnav/import-plan.tsv`, `--apply`,
  `--from-plan`. Outcomes: imported, already held, unverified, undecidable.
  Copy (default) or `--move`. Tags applied per file (no accumulation bug).
- `importers/bibtex.py`: `litnav import-bib refs.bib [--pdf-root]`, reading
  Zotero/JabRef `file` fields.
- `importers/legacy.py`: `litnav import-legacy <dir> --pdf-dir <dir>`:
  `ref.bib`, `tags.txt` (comma-separated, one line), `summary.json`,
  `extracted_text.txt`, project symlink sets, `confidential` tag → sharing;
  reports anything it can't map.
- `litnav fetch <doi|key...>`: open DOIs in the browser.

Tests: mocked Crossref/arXiv responses; the cited-DOI trap (a document citing
another paper's DOI on page 1); duplicate under a new filename; tags per file.

Done when: a test library built from 20 real PDFs imports correctly, and a
legacy conversion of a real litman directory produces one item per old item
with nothing silently dropped (a count table at the end).

### M3. Text, sections, chunks, index, search

- `extract.py`: full text via pdftotext/pypdfium2, OCR fallback when there is
  no text layer, `text_source` recorded; `confidential` items never extracted.
- `sections.py`: heading detection on plain text: numbered headings
  (`1`, `1.`, `2.3`, roman numerals), known unnumbered headings (Abstract,
  Introduction, Data, Methods, Results, Discussion, Conclusion(s), Summary,
  Acknowledg(e)ments, References, Appendix), guards against false positives
  (figure/table captions, equation numbers, page headers repeated on every
  page). Runs at index time on `text.txt` (changed in build: the tracked
  file stays as extracted); `sections_detected` is stored in the index.
- `chunks.py`: § 9 rules; fallback to fixed passages; IDs
  `<key>#<section>#c<n>`; abstract and caption chunks.
- `index.py`: schema (items, authors, tags, relations, sections, chunks,
  chunks_fts, edges, outside_works, meta_hashes); incremental by content hash.
- `embed.py`: fastembed (small BGE), vectors in `.litnav/vectors.npy` with
  an id list and the model name; summary vectors and chunk vectors.
- `search.py`: `keyword` (FTS5 BM25, quoted terms required), `semantic`,
  `hybrid` (reciprocal rank fusion), paper mode, filters, `similar`.
- CLI: `extract`, `index [--rebuild]`, `search`, `similar`.
- `eval/`: question file format and `litnav eval` (recall@k, MRR per config).

Tests: section detection on fixture texts (AMS, AGU, QJ, Copernicus styles,
an OCR'd scan, a paper with no headings); chunks never straddle; reference
list excluded; FTS query with quotes; RRF merge.

Done when: on the migrated library, section detection succeeds on ≥ 75% of
papers with text (report the rate), and hybrid passage search beats the
old fixed-passage search on the eval set (or the eval shows why not).

### M4. References, rename, graph

- `refs.py`: parse `[@key]`, `[@a; @b]`, `[@key, sec. 4]` and bare `@key`
  in Markdown (skipping code spans, code blocks, emails); `check-refs`;
  backlinks into the index.
- `rename.py`: `litnav rename old new`: moves directory, PDF, variants;
  rewrites references in notes/concepts/topics/projects; records alias;
  refuses collisions; dry run first.
- `litnav merge keep drop`: drop becomes an alias of keep; its PDF goes to
  `_variants/`.
- `graph.py`: OpenAlex resolution (DOI → work id) cached in `meta.toml`
  `[ids]`; reference lists cached; internal edges; outside works cited by ≥ N
  library papers (candidates); `graph.json` for the web view.
- `litnav convert-refs`: backticked text matching a real key → `[@key]`,
  with a diff.

Tests: reference parsing edge cases (a key that is a substring of another;
emails; code); rename rewrites and alias resolution; graph with a mocked
OpenAlex.

### M5. MCP server and skills

- `mcp_server.py` (stdio): read-only tools `search_literature`,
  `get_chunk`, `get_section`, `get_paper`, `related_papers`, `list_papers`,
  `reading_due`, `get_note`; write group (`add_tag`, `set_reading`, ...)
  only with `--allow-writes`. Every tool goes through `items(purpose)`;
  `--serve-others` restricts to `serve_others`.
- `skills/*/SKILL.md`: curator, researcher, reader, explorer (§ 13),
  each naming the CLI/MCP tools it uses and its boundaries.
- `.claude-plugin/plugin.json`, `.mcp.json`: install as a Claude Code
  plugin.

Done when: in Claude Code with the plugin installed, the researcher skill
answers a real question with chunk-ID evidence, and a confidential test item
is invisible to every tool.

### M6. Web app

- `web/app.py`: `/api/items`, `/api/item/<key>`, `/api/search`,
  `/api/graph`, `/api/tags`, `/api/reading`, `/api/note/<key>`, writes
  (`POST /api/item/<key>/tags`, `/sharing`, `/reading`), `/pdf/<key>`,
  `/api/ask` (server-sent events). Binds 127.0.0.1; a per-run token in the
  page guards writes from other local origins.
- Writes journalled to `.litnav/journal.jsonl`; debounced git commit.
- `web/static/`: tabs Library, Search, Paper, Graph, Tags, Reading, Ask.
  Graph: port the existing canvas network with the degree-aware spring force,
  fed from `/api/graph`, PDF buttons via `/pdf/<key>`.
- `ask.py`: `api` back end (Anthropic tool runner over the same functions the
  MCP server exposes) and `claude-code` back end (`claude -p` with
  `--output-format stream-json` and the litnav MCP server). Evidence links
  open the Paper view at the passage.

Done when: `litnav serve` on the migrated library shows every view, edits a
tag (committed), opens a PDF from the graph, and answers an Ask question
through at least one back end.

### M7. Reading, recall, discovery, knowledge layer

**Done 2026-11-15**, following an advisor review: `eval` first; one gate
(`derived.outbound`) for all text bound for a model; batches submitted and
collected on a later run, re-checking sharing; Claude Code runs with no
transcripts or user settings; the to-fetch list is a TSV keyed on DOI with
`skip` rows kept. Deferred: quizzes as a command, the legacy Markdown
recall-queue import (done by a private script instead).

- `reading.py`, `recall.py`: list with depths, done records, Leitner boxes
  with working days and the medium interval, `litnav due`, `read`, `recall
  rate <id> <rating>`, quizzes logged to `quizzes/`. Legacy Markdown queue
  import.
- Discovery: `litnav candidates` (outside works cited by the library),
  `litnav explore` (S2 recommendations from seeds), a `discovery/to-fetch`
  list; `fetch` opens DOIs.
- `derived.py`: `summarize` (batch API) with provenance, honouring
  `send_out`; `themes --update`; derived files in `derived/`.

### M8. Release and department trial

- `docs/quickstart.md`: from an empty directory, and from a Zotero export.
- Test on a clean machine account (and Linux in CI; Windows best effort).
- GitHub Actions: tests on macOS and Linux, Python 3.11–3.13.
- Tag v0.1.0; announce to the department with the quickstart.

## Migration of an existing litman library (the author's own)

Run after M2 and again after M3, into a **new** library directory; the old
data is read, never written. Compare: item counts, keys, tags, sharing,
summaries, PDFs resolvable, text present. Private workflow files (reading
lists, notes, recall queue) are migrated by a separate private script, not
by code in this repo, where they would need special cases.

## Working rules

- Tests first for every rule in § 14.
- No network in tests; record fixtures.
- No private data in this repo: fixtures use public open-access papers or
  synthetic text.
- Commit at the end of every milestone, and in between whenever a piece works.
- Questions an advisor can't settle are queued below, not blocked on.

## Revisions after review (2026-11-15)

An advisor review of the first draft changed these points; they override the
milestone text above where the two differ.

**Data and IDs**
- Chunk IDs are `<key>#<section-slug>#<hash8>`, the hash over the chunk's
  normalised text, so a re-chunk keeps IDs for unchanged text. Keys resolve
  through aliases. Every piece of evidence stores its quote and `text.txt`
  offsets as well, so a dead ID can be re-found by quote. The index records a
  chunker version.
- Vectors are BLOBs in SQLite keyed by a hash of (model, header + text):
  transactional, deletes handled, only changed text re-embedded. No
  separate `vectors.npy`.
- All file writes are atomic (temp file + `os.replace`); SQLite runs in WAL
  mode; one lock file guards writers (CLI, web, MCP).
- The index location is configurable (`index_dir`); `init` puts it in the
  user cache directory when the library sits in a synced folder (Dropbox,
  OneDrive, iCloud), which corrupts SQLite.
- `track_text = false` keeps `text.txt` locally, gitignored.

**Sharing**
- Each chunk row carries its item's sharing level; every search and fetch
  goes through one query function that takes a purpose.
- `net.py` is the only module that opens connections, and every call names a
  purpose and the items it concerns. A test monkeypatches `socket` and fails
  any other network access.
- Confidential import: `_inbox/_confidential/` (or `import --confidential`)
  skips text extraction and every lookup.
- `open` is set only for explicit CC licences reported by OpenAlex, not from
  open-access status alone.
- The docs say plainly that the levels guard litnav's own code paths, not an
  agent that has file access to the library.

**Web**
- Validate the `Host` header on every request (DNS rebinding); check
  `Origin` on writes; no CORS headers; a per-run token required on all of
  `/api`, `/pdf` and `/api/ask`; Markdown and extracted text sanitised
  before rendering.
- Git: commit only the paths a write touched, one commit per write, and skip
  (with a warning) while a merge, rebase or `index.lock` is in progress.

**Ask**
- The `api` back end first, in-process, over the same functions as the MCP
  server. `claude-code` second: run with only the litnav MCP tools allowed,
  a strict MCP config and a working directory outside the library.

**Search**
- FTS5: every user token quoted (so `-`, `:`, `AND` are literal), NFKC
  normalisation (ligatures), line-break hyphens rejoined, diacritics folded,
  no stemming at first (acronyms), title and authors as weighted columns.
- Paper-level search falls back to title + abstract + first chunk when an
  item has no summary.
- Embedding is resumable with progress; keyword search works while it runs.
  `litnav doctor` checks pdftotext, tesseract, git, the model download, API
  reachability.

**Order and scope**
- A minimal `reading.py` (list + due) moves to M1, so MCP and the web app
  can use it.
- MCP: `list_papers` filters by author, year and title words; result sizes
  capped; `get_section` paginated.
- Windows: `init` writes `.gitattributes` (`* text=auto eol=lf`), copes with
  git missing or unconfigured; `key_style = keep` rejects keys that are
  unsafe as filenames. CI includes `windows-latest`.
- Zotero `file` fields: `;`-separated, `desc:path:mime`, escaped colons.
- Deferred past v0.1: font-size heading cues, GROBID, model-detected
  boundaries, rerank, `--serve-others` and the MCP write group (the purpose
  table and its tests stay), new OCR backends beyond tesseract detection,
  `merge`, citation-neighbourhood filters, candidate rings in the graph, bulk
  assignment.
- The retrieval eval questions live in the user's library
  (`eval/questions.toml`), never in this repo: they describe a private corpus.

## Queued questions for the author

1. **Eval questions (needed by M3).** 20–50 real questions you would ask your
   library, each with the papers (ideally passages) a good answer must find.
   Only you can write these; they gate the search comparison.
2. **Default for `licensed` text going to a model.** Summaries and Ask send
   the text of licensed papers to Anthropic. Some publishers' text-mining
   terms restrict this. Proposed: allowed by default (as now), with `init`
   asking once and the answer stored in `library.toml`. OK?
   **Answered 2026-11-15:** ask once at `init`, then remember
   (`licensed_to_models`). Built.
3. ~~Summaries without an API key.~~ Answered 2026-11-15: yes, through
   Claude Code, defaulting to Haiku, with a warning that it is slower than
   the Batch API (design § 17). Built in M7.

## Next (2026-11-15)

All ten done on 2026-11-15 (v0.1.0 tagged after item 6).

1. Migrate the author's own reading data into their library (private script, not in this repo).
2. Freeze the old litman data; the author moves to litnav this week.
3. Fold new papers into the theme report (`themes --update`).
4. Test a clean install from the git URL.
5. CI on Python 3.11–3.13.
6. Tag v0.1.0; draft the department announcement.
7. Web app: outside candidates in the Graph view, a Discovery tab with the to-fetch list.
8. `litnav quiz`: quiz logs per paper.
9. `summarize --jobs N`: parallel Claude Code summaries.
10. **Discuss** a paper instead of (or as well as) a quiz: the reader explains it
    in their own words first; Claude probes, corrects from the paper's own
    passages with chunk IDs, and logs what was solid and what was shaky.
    Claude Code first (`/litnav:discuss <key>`), web app later.
