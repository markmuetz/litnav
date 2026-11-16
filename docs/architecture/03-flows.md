---
title: "litnav architecture 3: Data flows"
subtitle: "Step by step through the six pipelines"
date: "2026-11-15 (v0.1.0)"
---

Each section has a diagram, then the steps with the functions that do them,
then the things worth knowing (failure modes, invariants).

# 3.1 Import: a PDF becomes an item

![Import. A plan is written first; nothing is created until you apply it.](diagrams/import-flow.pdf){height=78%}

**Entry points.** `litnav import [FILE...] [--doi D] [--key K] [--tag T]
[--apply | --from-plan]` (`commands/importing.py`), and `import-bib` for a
Zotero/JabRef/Mendeley `.bib` with `file` fields (`importers/bibtex.py`).

1. **Collect.** `importer.inbox_files()` walks `<pdf_dir>/_inbox/`; the first
   subdirectory is a tag (and a project, if `projects/<TAG>/` exists).
   `_inbox/_confidential/` is special-cased before anything is read.
2. **Read the front.** `extract.head()` runs `pdftotext` on the first
   pages; if there is no text layer and OCR is on, OCR (macOS Vision or
   tesseract) instead.
3. **Identify** (`identify.identify`), cheapest and most certain first:
   DOI in the text (or an AMS SICI-style filename) -> Crossref; arXiv id ->
   arXiv (key gets `_preprint`); else a bibliographic title query to
   Crossref with the opening lines.
4. **Verify** (`identify.verify`): the share of the resolved title's
   distinctive words that appear in the front text must reach
   `verify_identifier` (0.6) for identifier routes, `verify_title_query`
   (0.75) for guesses. This catches a *cited* DOI being taken for the
   paper's own.
5. **De-duplicate** (`importer.Catalogue`): by PDF SHA-256, DOI, normalised
   title, and key (case-insensitive, including aliases). A duplicate is
   `held`.
6. **Derive the key** (`keys.derive_unique`) from the verified metadata; on a collision
   the next title word is added (`smith2021coldpools`), a letter only as a last resort.
7. **Plan.** Rows go to `.litnav/import-plan.tsv` and are printed:
   `ok`, `held`, `low-confidence`, `no-match`.
8. **Apply** (`importer.apply`): for `ok`, `Library.create_item`, write
   normalised `ref.bib`, `meta.toml` (sha256, tags, sharing = the library's
   `default_sharing`), move (inbox) or copy the PDF to `<pdf_dir>/<key>.pdf`,
   extract `text.txt`; for `held`, move the file to
   `_variants/<key>.<date>.pdf`; others stay put. Finally
   `discovery.prune()` drops imported DOIs from the to-fetch list.

**Worth knowing.** Nothing is created by the dry run. `--from-plan` applies
the saved plan without re-querying (so a slow network run is not repeated).
PDFs are never deleted. Confidential imports never touch `pdftotext` or the
network.

# 3.2 Index and search

![Indexing (left) feeds search (right). Dashed arrows: search reads what indexing wrote.](diagrams/index-search-flow.pdf){width=100%}

**Indexing** (`litnav index`, `index.update` then `embed.update`):

1. For each item, compare `index.signature(it)` with the stored `sig`; skip
   unchanged items; delete rows of removed items.
2. `sections.parse(text)` finds headings (numbered and known names such as
   Abstract, Introduction, Methods, References; wrapped numbered headings are
   joined), the abstract, and figure/table captions.
3. `chunks.chunk()` packs paragraphs into ~300-word chunks (max ~420, ~15%
   overlap) inside a section, never across a top-level section; abstract and
   captions are their own chunks; references are not chunked; papers without
   structure get fixed windows.
4. Rows go into `items`, `items_fts`, `sections`, `chunks`, `chunks_fts`;
   each chunk row carries the item's sharing level.
5. `embed.update()` embeds `header + text` for every chunk whose `vhash` has
   no vector, and `title + summary` (or abstract) per paper, in batches,
   resumable.

**Searching** (`search.search` for passages, `search.papers` for papers):

- **keyword**: the query is tokenised; every token is quoted so FTS5 operators
  in user input are inert; `"quoted phrases"` are required, other words
  optional; ranked by BM25.
- **semantic**: the query is embedded (with the model's query prefix) and
  compared by cosine with a cached, normalised matrix of all vectors
  (`embed.matrix`, numpy).
- **hybrid** (default): both lists (pool of 100 each) are fused by
  reciprocal rank fusion, score = sum of 1/(60 + rank).
- **filters**: purpose (sharing), year range, tag, author, project, keys.
  Paper-level results collapse chunk hits per key.

Results are dicts with `id` (chunk ID), `key`, `citation`, `section`,
`text` (verbatim), offsets and `score`. `litnav similar <key>` uses the paper
vectors.

# 3.3 Ask: a question answered from the library

![Ask. The model only ever sees the library through the read-only tools.](diagrams/ask-flow.pdf){width=100%}

1. The web page `POST`s `/api/ask` with the question and recent history
   (token and Origin checked, document 4).
2. `ask.stream_answer` picks the backend from `[ask] backend`:
   - **claude-code** (default): writes a temporary MCP config naming
     `python -m litnav.cli --library <root> mcp`, then runs `claude -p` in a
     temporary directory with built-in tools disabled (`--tools ""`),
     `--strict-mcp-config`, `--allowedTools mcp__litnav`, no session
     persistence, no settings or slash commands. Its stream-JSON is mapped to
     events. After 2 s without output a `ping` is sent (an SSE comment the
     page ignores), so a closed page is noticed and `claude` is killed.
   - **api**: a streaming Messages-API loop (`MAX_TURNS` 14) with
     `TOOL_DEFS` mirroring the MCP tools; each tool call is validated against
     its schema and run on `Tools(lib, purpose=send_out)`.
3. Events (`status`, `tool`, `tool_result`, `text`, `done`, `error`) are sent
   to the page as server-sent events; `app.js` renders Markdown and turns
   `[@key]` and chunk IDs into links to the Paper view.

**Worth knowing.** Both backends see only what `Tools` returns with purpose
`send_out` (mapped to `send_out_strict` if you said no to licensed text):
confidential items are invisible, and with `licensed_to_models = false`
licensed items show metadata but no text. The system prompt requires `[@key]`
plus chunk ID for every claim.

# 3.4 Summaries and themes

![Summaries and the theme report. Red boxes send text to a model.](diagrams/derived-flow.pdf){height=60%}

1. `derived.select()` picks items with text and without a current summary
   (filters: `--tag`, `--limit`; `--force` redoes; `--stale` picks summaries
   whose text sha changed).
2. `derived.outbound()` checks **every** selected item against
   `lib.purpose(SEND_OUT)`; one refusal stops the run before anything is sent.
3. Route:
   - `--via api`: one Batch API request per paper (text truncated to a token
     budget); the batch id is saved in `.litnav/summarize_batch.json`, so a
     later run collects it; collection re-checks sharing and that the text
     sha still matches.
   - `--via claude-code`: one `claude -p --json-schema` per paper, locked down
     like Ask; `--jobs N` runs N at a time; an auth or usage-limit error
     (`Stop`) halts all workers, keeping finished summaries.
4. `write_summary` writes stable JSON with provenance (model, route, text
   sha, truncated) and then the index is updated and re-embedded.
5. `litnav themes` sends all shareable summaries to Opus and writes
   `derived/themes.md`; `--update` sends only papers the report does not cite
   yet, with the existing report, and must keep existing themes verbatim.

# 3.5 Citation graph and discovery

![Graph and discovery. Purple boxes are metadata services; nothing downloads PDFs.](diagrams/discovery-flow.pdf){width=100%}

1. `litnav graph` (`graph.build`): for every item with a DOI that allows
   `LOOKUP`, fetch its OpenAlex work (id cached in `meta.toml [ids]`) and
   reference list. Internal references become `edges`; outside works cited by
   at least `--min-cites` library papers become **candidates** with their
   `citers`. An explicit CC licence makes an item `open` if its sharing was
   never set by hand. Output: `graph.json` and the `edges` table.
2. `litnav candidates` reads `graph.json`, drops what the library holds
   (`discovery.Holdings`: DOI, folded title, or author+year prefix with
   title overlap -> `held` / `maybe`) and what you skipped.
3. `litnav explore <keys>` asks Semantic Scholar for recommendations per seed
   (only the seeds' DOIs are sent; seeds must allow `LOOKUP`), merges them
   (papers recommended for several seeds rank first), and filters the same
   way.
4. `--add N`, `to-fetch add`, or the web Discovery tab append rows to
   `discovery/to-fetch.tsv` (deduped on DOI or title). `skip` rows stay so a
   paper is not proposed again.
5. `to-fetch open` opens DOIs in your browser; you save the PDF into
   `_inbox/`; import prunes the row.

The web Graph tab draws `graph.json` (force layout in `graph.js`) and, with
the tick-box, candidates as hollow rings linked to their citers.

# 3.6 Reading, recall, quizzes and discussions

![Reading and recall. Only you mark a paper read.](diagrams/reading-flow.pdf){width=100%}

- **Reading list** (`reading.py`, `reading/list.toml`): `read add <key>
  --depth deep|read|skim|scan|comment`; status `to read` -> `reading` ->
  `done`, with the depth actually done.
- **Recall** (`reading/recall.toml`): `read done` on a deep or read paper
  creates a card in box 1. `recall <id> forgot|hard|medium|easy`: easy
  promotes (max box 4), medium stays with a longer interval, hard stays,
  forgot returns to box 1. Intervals: 1 working day, 1 week, 1 month, 3
  months; never due at a weekend. Concept cards use `concept:<slug>`.
- **Due** (`litnav due`, MCP `reading_due`): cards due today and papers being
  read.
- **Quizzes and discussions** (`quizzes.py`, `quizzes/<key>.md`): `quiz start
  <id> [--discuss]` appends a dated section; `/litnav:discuss <key>` (plugin
  command + reader skill) prepares quietly, lets you explain first, probes,
  corrects only from passages with chunk IDs, logs the session, and can rate
  a due card on the gist with your agreement.

# 3.7 End to end: a day in the life of one paper

One paper through every flow above, with the command Claude runs at each
step and what it writes. You only talk to Claude and download the PDF.

1. **Discovery (3.5).** Asked what is missing on cold pools, Claude runs
   `litnav candidates` (works cited by several library papers, from
   `.litnav/graph.json`), you pick one, and `litnav to-fetch add <doi>
   --source candidates:7` then `litnav to-fetch open 1` open its DOI in your
   browser. Only the DOI left the machine (OpenAlex had the reference lists
   already). You save `JAS-D-23-0123.pdf` to Downloads and tell Claude
   "got the cold-pool paper, put it in UPFLO".
2. **Import, dry run (3.1).** `litnav import --from-downloads` lists PDFs
   from the last two hours, skipping byte-identical re-downloads and names
   like *draft* or *review*; nothing is read or sent yet. Claude checks the
   list is what you meant, then re-runs it with `--apply`. Now the plan step
   runs: `identify.py` finds DOI `10.1175/...` on page 1, `net.Client`
   (purpose `lookup`) asks Crossref for the record, the resolved title's
   distinctive words are checked against the PDF's text, and the `Catalogue`
   finds no existing item with that SHA-256, DOI or normalised title. The
   proposal, key `smith2024cold` (`<surname><year><first content word>`), is
   written to `.litnav/import-plan.tsv`.
3. **Import, apply (3.1).** `importer.apply` creates
   `papers/smith2024cold/` with `ref.bib` (normalised to house style) and
   `meta.toml` (`pdf_sha256`, `identified_via = "doi"`; with no `sharing`
   line the item takes `default_sharing`), moves the PDF to
   `<pdf_dir>/smith2024cold.pdf`, extracts `text.txt` (pdftotext, or OCR for
   a scan), and `discovery.prune` drops the DOI from `to-fetch.tsv`. Claude
   then runs `litnav project add UPFLO smith2024cold --status "to read"`
   (`projects/UPFLO/papers.toml`).
4. **Index (3.2).** `litnav index` sees a new signature, detects sections
   in `text.txt`, cuts ~300-word chunks with IDs like
   `smith2024cold#3-2#9f1c04ab` (the hash is over the chunk's normalised
   text, so the ID survives re-chunking), writes `items`, `sections`,
   `chunks` and both FTS tables, copies the item's sharing level onto every
   chunk, and `embed.update` embeds the new chunks locally (fastembed). The
   index generation is bumped, so the web app and MCP server rebuild their
   cached vector matrices on their next search.
5. **Summary (3.4).** `litnav summarize smith2024cold --via claude-code`
   passes the text through `derived.outbound()`, which checks the level
   allows `send_out` (and, with `licensed_to_models = false`, refuses
   licensed text), runs `claude -p` with Haiku on your login, and writes
   `summary.json`. Re-indexing adds the summary to `items_fts` and gives the
   paper a better vector for paper-level search and `similar`.
6. **Search and answer (3.2, 3.3).** Days later you ask about cold-pool
   spreading rates. Claude calls `search_literature` over MCP (purpose
   `send_out`; `Tools._live()` re-checks `meta.toml`), gets this paper's
   passage by keyword and by meaning fused with reciprocal rank, reads the
   section with `get_section`, checks your notes with `search_notes`, and
   cites `[@smith2024cold]` with the chunk ID and a short verbatim quote.
7. **Reading and recall (3.6).** You ask Claude to put it on your reading
   list (`litnav read add smith2024cold --depth read`, `reading/list.toml`).
   You write `notes/smith2024cold.md`; at the next `litnav index` its
   passages go in the `notes` table, private (sendable to Claude) because
   the paper is not confidential. When you say
   you've read it, `litnav read done` records the depth and date and adds a
   card to `reading/recall.toml` (box 1, due the next working day). A week
   later "let's discuss smith2024cold" runs the discussion protocol: you
   explain, Claude probes and checks against the passages, and `litnav quiz
   start smith2024cold --discuss` logs it in `quizzes/smith2024cold.md`.
   `litnav recall` rates the card on the gist and moves it between boxes.
