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
  sections.py            heading detection → text.md
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
  page). Writes `text.md` with `#`/`##` headings and a front matter block
  (`sections_detected: true|false`).
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

## Queued questions for the author

(Appended as they come up.)
