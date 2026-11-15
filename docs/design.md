# v2: a literature library for researchers and their agents (draft)

Status: **draft, 2026-11-15**. Name **litnav** (CLI `litnav`). MIT licence.
This is the design doc of a public, open-source repo, so it contains nothing
private: no paths, people or unpublished work. The build order is in
`docs/plan.md`.

## 1. What it is, and for whom

A local, file-based literature library: PDFs, clean metadata, extracted text
with its section structure, search by keyword and by meaning, reading lists
and spaced recall, discovery of papers you don't have, and a layer of notes
and concepts on top. It is built so that a researcher can use it from the
command line, and Claude (or any MCP-speaking agent) can use it through tools
and skills.

**Audience: anyone.** First trial: one university department. So:

- installs with one command, runs on macOS, Linux and Windows;
- works without Claude and without an API key (search, import, reading lists);
  Claude-backed features are optional extras;
- no personal paths, no assumptions about Dropbox, git hosting or a
  particular field's journals;
- each user owns their library; there is no server.

It replaces two systems that grew up separately: a library manager (storage,
bib, text, summaries, search) and a workflow layer (curation rules, reading,
recall, discovery). The rules those systems learned the hard way are carried
over as tests (§ 14); the code is not.

## 2. Principles

1. **Files are the source of truth.** Everything that matters is plain text in
   a directory that is a git repo: readable, diffable, hand-editable,
   recoverable. Nothing important lives only in a database.
2. **The index is derived.** SQLite (catalogue, full-text search, chunks,
   citation edges) and the embedding vectors can be deleted and rebuilt from
   the files. They are gitignored.
3. **Privacy is in the data model.** Every item has a sharing level (§ 6) and
   every code path that sends text anywhere, or serves it to someone else,
   checks it. It is not a convention that new code must remember.
4. **Interface first.** The CLI and the MCP tools are the contract. Skills and
   agents use them and never touch the files directly, so the internals can
   change behind them.
5. **Evidence, not assertions.** Search returns passages with stable IDs and
   verbatim text. Anything a model generates carries its provenance and is kept
   apart from both the sources and the user's own writing.
6. **Local by default.** Text extraction, OCR, embeddings and search run on
   the user's machine. Outside services are used for metadata (Crossref,
   OpenAlex, arXiv, Semantic Scholar) and, optionally, for Claude.
7. **No automated PDF downloading.** The tool opens a DOI in the browser and
   the user downloads it. Not scraping Google Scholar.

## 3. The library on disk

`litnav init <dir>` is a new user's first step. It creates:

```
<library>/                    git repo (private: see note below)
  library.toml                config (§ 3.3)
  papers/<key>/               one directory per item, flat
    ref.bib                   the bibliographic record, in house style (§ 8)
    meta.toml                 litnav's own fields: ids, tags, relations, sharing
    text.txt                  extracted text, as extracted (tracked); sections are found at index time
    summary.json              generated summary, with provenance (tracked)
  notes/<key>.md              the user's notes on a paper
  concepts/<slug>.md          concept notes
  topics/<slug>.md            topic notes
  projects/<name>/papers.toml project membership, status, project tags
  quizzes/<id>.md             quiz logs
  reading/                    reading list and recall state (§ 10)
  derived/                    generated knowledge layer (§ 11)
  pdfs/                       default PDF store (gitignored), flat <key>.pdf
    _inbox/                   drop new PDFs here; a subdirectory name is a tag
    _variants/                extra copies: annotated, re-downloaded, versions
  .litnav/                   index.sqlite, vectors, caches (gitignored)
```

**The library repo must stay private** if it tracks `text.txt` (extracted text
of licensed papers is redistribution if pushed somewhere public). `init` writes
this into the library's README, and `library.toml` can turn off tracking of
text (`track_text = false`) for users who want to publish their repo.

### 3.1 Does a flat store keyed by citation key work for everyone?

Yes, with four adjustments.

- **Scale is fine.** Filesystems handle 10⁴–10⁵ entries in one directory
  without trouble, and git handles 10⁴ small directories. File managers slow
  down past ~10k files, but nobody browses this by hand; that is what the CLI
  and `ls pdfs | grep` are for.
- **The PDF store is configurable, and linked by config, not symlinks.**
  `pdf_dir` defaults to `<library>/pdfs`, but can point at an existing synced
  folder (Dropbox, OneDrive). The PDF for `<key>` is always
  `<pdf_dir>/<key>.pdf`, so no per-item link is stored and nothing breaks on
  Windows (where symlinks need admin rights) or when the folder moves.
- **Keys are unique case-insensitively.** macOS and Windows filesystems are
  case-insensitive by default, so `huffman2020IMERG` and `huffman2020imerg`
  would be the same file. Uniqueness is checked on the case-folded key.
- **Renames are first-class and keep aliases.** A rename moves the directory
  and PDF, rewrites `[@old]` references in notes, and records `old` in the
  item's `aliases`, so old references and old `.bib` files still resolve.
  This replaces a separate "retired" directory for the losing side of a merge.

New users mostly already have PDFs (a folder tree, Zotero, Mendeley). They
do not reorganise anything by hand: they point an importer at it (§ 7), which
copies (default) or moves the files into the flat store.

### 3.2 `ref.bib` and `meta.toml`

Two small files per paper, each doing one job.

**`ref.bib`** is the bibliographic record: one BibTeX entry whose key is the
item's key, normalised to house style (§ 8). It stays BibTeX because LaTeX
escapes (`{\"u}`, `{{MJO}}`) survive it exactly and do not survive a
round trip through another format, and because researchers already read and
edit BibTeX. `litnav bib [keys|--project P]` concatenates entries for LaTeX
and Pandoc.

**`meta.toml`** holds what BibTeX has no place for. It replaces the old
system's `tags.txt`, side spreadsheets and symlinks:

```toml
aliases = []
sharing = "licensed"            # § 6
tags = ["cold_pools"]
added = 2026-11-15

[ids]                           # resolved identifiers, for the outside APIs
openalex = "W123"
s2 = "abc"
arxiv = ""

[relations]                     # § 5
preprint_of = ""
corrigendum_of = ""

[files]
pdf = true                      # false for physical copies
text_source = "pdftotext"       # or "ocr:tesseract", "ocr:vision", "grobid"
pdf_sha256 = "..."
pdf_path = "IPCC/ipcc2021wg1ch01.pdf"   # optional, relative to pdf_dir, for a PDF kept
                                        # outside the flat store
licence = "cc-by"               # from OpenAlex, when it reports an open licence
```

Every field is optional; an item with only a `ref.bib` is valid.

### 3.3 `library.toml`

Key convention options (§ 4), `pdf_dir`, the default sharing level, which OCR
backend, which embedding model, the contact email for polite API pools, and
whether Claude features are on. Nothing machine-specific is required: a
second machine clones the repo and sets `pdf_dir` in a local override
(`.litnav/local.toml`, gitignored).

## 4. Citation keys

Default convention `<author><year><word>[<part>][_<marker>]`:

- first author's surname, lowercased, accents folded (Volonté → `volonte`);
- **year of the issue**, not the online-first date; must be plausible
  (1800 … next year), which stops journal article numbers parsing as keys;
- first **content** word of the title (skip articles, prepositions,
  conjunctions, interrogatives, auxiliaries; the list is configurable);
- optional `<part>` digit for Part I/II; an acronym may replace an
  uninformative word (`hersbach2020era5`);
- optional marker: `_preprint`, `_supplement`, `_corrigendum`, `_draft`,
  `_slides`, `_poster`, `_manuscript`;
- corporate authors take the organisation (`ipcc2021wg1ch01`).

**A numeric suffix never means "duplicate"**: that namespace belongs to parts.
**Collision with a different paper** (same author, year and first word):
append the next content word (`smith2021coldpools`). Users with an existing
scheme (e.g. Better BibTeX keys) can keep theirs: `key_style = "keep"` takes
the key from the imported `.bib` and only validates it.

## 5. Item kinds and relations

Relations replace tags-plus-spreadsheets for pairs of items:

| relation | rule |
|---|---|
| `preprint_of` | a preprint is kept alongside its published version, never replaced (it may carry annotations). Published key is the one to cite. `litnav check-preprints` finds published versions (same DOI gaining a volume, arXiv's DOI, Crossref `is-preprint-of`, title match). Same-DOI kinds (e.g. AMS early online releases) never re-fetch metadata for the preprint. |
| `corrigendum_of` | its own item, sorted next to the parent; tools warn before quoting a parent with a corrigendum. |
| `draft_of` | a co-authored draft; `confidential` by default (§ 6). Versions go to `_variants/<key>.<date>[-<label>].pdf`; the current one is always `<key>.pdf`. |
| `supplement_of`, `part_of` | supplements, book chapters. |

Physical copies (books on paper) are items with `files.pdf = false`; they have
metadata and notes but no text, and nothing tries to fetch a PDF for them.

## 6. Sharing levels

Two questions decide what may happen to an item's text: may it go to an
outside model or service on the owner's behalf, and may it be served to
someone else (another person's agent, a group hackathon setup)?

| level | outside model/service | served to others | default for |
|---|---|---|---|
| `open` | yes | full text | open-access papers (set automatically from OpenAlex's licence) |
| `licensed` | yes | metadata, abstract, summary only | every other imported paper |
| `private` | yes | nothing | things the owner wants kept to themselves |
| `confidential` | **no**: no text extraction, no summary, no OCR, no embedding, no DOI or title lookup | nothing | co-authored drafts, anything the owner marks |

Enforced in one place: `Library.items(purpose=...)`, where `purpose` is
`local`, `send_out` or `serve_others`. Every command and MCP tool asks for the
purpose it needs. Tests assert that no code path reaches an outside service
without it.

**Things that never enter the library** are documented, not just enforced:
manuscripts under peer review and students' assessed work. Journals and
universities restrict putting these into AI tools; keeping them out of the
library means no command can send them anywhere. The papers they cite can come
in as usual.

## 7. Import

Sources: PDFs (`_inbox/` or any path), a `.bib` with `file` fields (Zotero via
Better BibTeX, JabRef, Mendeley export), and a converter from the old system
(§ 15).

Identification ladder, each step verified before it is applied:

1. **DOI** in the first pages → Crossref.
2. **arXiv id** in the text or filename → arXiv API; gets `_preprint`.
3. **Title query** against Crossref from the document's opening lines; a guess,
   so it must clear a higher bar (distinctive title words found in the text:
   0.75 against 0.6 for an identifier).
4. **OCR first** if there is no text layer, then 1–3 again.

Verification is what catches a *cited* DOI mistaken for the document's own.
Unverified proposals are reported, never applied.

Before anything is created, duplicates are checked by DOI, PDF hash and
normalised title, not only by key, so the same paper under another filename
does not import twice. Outcomes: imported; already held (file goes to
`_variants/` or is reported); undecidable (stays in `_inbox/` with a reason).
Dry run by default; `--apply`; `--from-plan` applies exactly what a dry run
produced.

## 8. Metadata hygiene

Ported from the old `normalise_bib`, as rules, not snapshots:

- field order, layout, delimiters; `"{X}"` → `{{X}}`, never `{X}` (only
  content braces protect capitals from a bibliography style);
- author names against an authority file keyed on `(surname, first initial)`,
  so unseen names still resolve; groups marked `split` are never merged;
- initials split (`TN` → `T N`) only when the surname is normally cased, so
  `BASTIN, MARK E.` doesn't become `M A R K`;
- journal names against an abbreviation table.

Runs after every write to `ref.bib`; idempotent; a `litnav check` reports
anything it can't fix (missing DOI, implausible year, key not matching the
convention).

## 9. Text, structure and chunks

**Extraction** writes the raw text to `text.txt`; section structure is
detected at index time (`sections.py`, versioned) and stored in the index, so
improving the detector never rewrites tracked files. A hand-sectioned
`text.txt` (Markdown headings, `sections = "manual"` in meta.toml) is used as is:

1. Born-digital PDF: the text layer (pdftotext or pypdfium2; **not PyMuPDF**,
   which is AGPL and would bind the project's licence).
2. Section detection by heuristic: numbered headings (`1. Introduction`,
   `3.2 Data`), common unnumbered ones (Abstract, Conclusions, References),
   and font-size cues. In the old corpus a regex alone found an Introduction
   heading in ~80% of papers.
3. Scans: OCR (ocrmypdf + tesseract everywhere; Apple Vision on macOS), then 2.
4. Optional backends: GROBID, if installed, for users who want it; a small
   model that returns **boundaries only** (titles and offsets) for papers the
   heuristic fails on, never rewritten text.

`text_source` records which path made the text, because OCR'd equations and
captions are unreliable and summaries built on them should say so.

**Chunks** (built into the index, not stored as files):

- split by section; long sections at paragraph boundaries; short sections
  merged with a neighbour; never across a section boundary;
- ~200–400 words, ~15% overlap within a section;
- abstract and each figure caption are chunks of their own;
- reference lists are not embedded (they still feed the citation graph);
- papers with no detected structure fall back to fixed-size overlapping
  passages, so nothing is worse than today;
- stable IDs `<key>#<section>#c<n>` (e.g. `smith2021cold#4.2#c3`), with
  metadata: key, authors, year, section title, position;
- each chunk is embedded with a header: `Smith et al. 2021 — <title> —
  4.2 Results`.

## 10. Reading and recall

Ported from the workflow layer, as data plus commands:

- **Reading list** with a planned **depth**: `deep`, `read`, `skim`, `scan`,
  plus `comment` (someone's draft). Done entries record the depth actually
  done and the date. Only the user marks a paper read.
- **Recall** by Leitner boxes (1 working day, 1 week, 1 month, 3 months),
  ratings forgot/hard/medium/easy, never due at a weekend. Cards are for the
  gist (question, headline answer, where it sits, verdict); details get one
  check soon after reading, then are let go. Concepts get cards too
  (`concept:<slug>`).
- **Quizzes** check comprehension just after reading; logged per paper; the
  log is read before the next quiz so it goes deeper.
- **Triage** cards for the reading list ("still want this?", snooze).

State lives in `reading/list.toml` and `reading/recall.toml`;
`litnav due` prints everything due today in one summary.

## 11. Knowledge layer

Two layers, never mixed:

- **The user's own writing**: `notes/`, `concepts/`, `topics/`, `projects/`.
  Tools read it (it is the best source for recall prompts) but never write
  content into it; they only create files from templates when asked.
- **Generated**: `summary.json` per paper, and in `derived/` the theme report,
  concept maps, open questions and gap analyses. Every generated item records
  model, date, inputs (keys or chunk IDs) and a hash of the inputs, so it can
  be refreshed when sources change. Every claim in it cites `[@key]` or a chunk
  ID. Gap analyses are prompts for the user to check, not findings.

Theme reports update incrementally (fold in new papers, leave existing themes
untouched) so diffs stay readable.

### References in Markdown: `[@key]`

Pandoc citation syntax everywhere: `[@smith2021cold]`, `[@smith2021cold,
sec. 4.2]`, `[@a2020x; @b2021y]`, or `@smith2021cold` in running text.
Standard (Pandoc, Quarto, Obsidian, Zotero users know it), renders to a
bibliography straight from `litnav bib`, and parseable without guessing. It
lets the tool:

- validate every reference against the catalogue (`litnav check-refs`);
- rewrite references on rename;
- build backlinks: which notes, concepts and topics cite a paper;
- link references in rendered views.

Chunk IDs are a separate, machine-level form used in evidence. Prefer
the bracketed form in anything that may be pasted into GitHub issues or PRs,
where a bare `@word` notifies a user.

## 12. Index and search

`.litnav/index.sqlite`: items, authors, tags, relations, sections, chunks,
**FTS5** over chunks (BM25 keyword search; replaces a separate word index and
`rg`), citation edges and outside works from OpenAlex. Vectors in a NumPy
file beside it (a few tens of thousands of chunks needs no vector database).
Updates are incremental by content hash; `litnav index --rebuild` starts over.

Search:

- `litnav search "<query>"`: hybrid by default (BM25 + embeddings, merged
  by reciprocal rank); quoted terms are required matches (acronyms, scheme
  names, case studies).
- `--papers` ranks papers by summary embedding ("which papers are about X");
  the default ranks passages ("where does someone say X").
- filters: year range, author, tag, project, sharing level, citation
  neighbourhood (`--cites K`, `--cited-by K`).
- `litnav similar <key>`.
- optional rerank of the top ~50 by a stronger model.

Results always carry chunk IDs, citations and verbatim text. Embeddings match
topic, not claims ("X strengthens Y" finds "X weakens Y"), so results are
candidates to read.

Local embedding model by default (fastembed, small BGE-class model), name
stored with the vectors; changing model means re-embedding.

**Evaluation is part of the build**: `eval/questions.toml` holds questions with
the passages that should come back; `litnav eval` reports recall@k for each
retrieval configuration. Every change to chunking or ranking is judged by it.

## 13. Interface: CLI, MCP, skills

### CLI (sketch)

```
init  import  check  fix  rename  merge  bib  open  fetch
extract  index  search  similar  graph
read  due  recall  quiz
explore  candidates
summarize  themes  derive
eval  export
```

`fetch` opens DOIs in the browser for the user to download; it never
downloads PDFs itself.

### MCP server

Read-only tools, safe to give any agent:

- `search_literature(query, k, filters)` → chunks with IDs, citations, section,
  verbatim text
- `get_chunk(id)`, `get_section(key, section)`
- `get_paper(key)` → metadata + summary
- `related_papers(key, direction)` → citation-graph neighbours
- `reading_due()`, `get_note(key)`

Write tools (import, tag, rename, recall ratings) are a separate tool group,
off unless enabled. All tools honour the sharing level for the caller's
purpose; a server started for others (`--serve-others`) only ever sees
`open` text and `licensed` abstracts and summaries.

### Skills

Shipped as a Claude Code plugin (skills + MCP server + hooks), installed
alongside the Python package (`uv tool install litnav`).

| skill | job | boundaries |
|---|---|---|
| **curator** | import, keys, metadata hygiene, dedup, preprints, corrigenda, drafts | follows § 4–8; dry-run first; never reads confidential items |
| **researcher** | multi-step literature questions: search, read passages, synthesise | returns evidence (claims with chunk IDs and short quotes), a fresh instance per question, never reads whole papers |
| **reader** | reading list, depths, quizzes, recall reviews, concept cards | cards from the user's notes first; grades on the gist; only the user marks things read |
| **explorer** | discovery: Semantic Scholar recommendations, OpenAlex references, candidates the library cites but lacks, interest ratings, the to-fetch list | metadata only; opens DOIs, never downloads |

Layering: files → index → CLI and MCP tools → skills and agents. Agents keep
their shared working memory (hypotheses, task lists) outside the library, so
speculation never mixes with sources.

## 14. Rules carried over, as tests

Each is a test in the new repo before the code that satisfies it:

- key derivation: issue year, plausible year, content-word skipping, accents,
  parts, markers, corporate authors, collisions, case-insensitive uniqueness
- import: cited DOI not mistaken for the document's own; title match needs the
  higher bar; same paper under another filename detected; arXiv → `_preprint`
- import with a subdirectory tag does not leak tags between files (an old bug)
- bib: `"{X}"` → `{{X}}`; initials not split in all-caps names; `month = jan`
  stays a bare macro; idempotence
- preprints: never replaced; same-DOI kind never re-fetched
- sharing: no outside call for `confidential`; `serve_others` returns no
  licensed full text
- references: `[@key]` parsing, rename rewriting, aliases resolve; a
  substring of one key is not a citation of another
- recall: never due at a weekend; medium falls between hard and easy
- chunks: never straddle a section; reference list excluded

## 15. Migrating from the old system

`litnav import-legacy <old-data-dir> --pdf-dir <dir>`, dry run first:

- `ref.bib` kept (re-normalised); `tags.txt` → `meta.toml`; `summary.json` kept
- `extracted_text.txt` → `text.txt`; OCR'd text kept (no re-OCR)
- preprint and draft spreadsheets → `relations`; draft items `confidential`
- PDF symlinks dropped: `pdf_dir` points at the existing flat store, already
  named `<key>.pdf`
- reading list and recall queue (Markdown) → `reading/*.toml`
- notes, concepts, topics copied; backticked text that matches a real key
  becomes `[@key]`, everything else untouched; the diff is shown for review
- citation cache → index; pruned of keys that no longer exist

Both systems run side by side until the new one does everything the old one
does; then the old one is frozen.

## 16. Non-goals

No hosted service or multi-user sync (git is the sync; the web app of § 16a
is localhost only). No automated PDF fetching. No Google Scholar. No PDF
reader or annotator. No reimplementation of a reference manager's word
processor plugins: `.bib` export covers LaTeX, Pandoc and most others.

## 16a. Web app (`litnav serve`)

A localhost single-page app over the library: one process, started with
`litnav serve [--port 8765]`, opening the browser. It binds to `127.0.0.1`
only, because it serves full text and PDFs of licensed papers.

**Server**: a small ASGI app (Starlette/FastAPI) over the same `Library` API
as the CLI. A JSON API, plus the PDFs at `/pdf/<key>` (which is what makes the
graph's PDF buttons work, where `file://` links are refused by browsers).

**Views** (one page, tabs, no build step: plain JS modules):

- **Library**: sortable, filterable table (title, authors, year, journal,
  tags, sharing, reading status). Filters combine: tag, project, year range,
  text query.
- **Search**: the hybrid search of § 12, with passages, section titles and
  chunk IDs; a toggle for paper-level results.
- **Paper**: metadata, summary, tags (editable), relations, citations in and
  out, the user's notes (rendered Markdown with `[@key]` links), backlinks,
  similar papers, open PDF, copy BibTeX.
- **Graph**: the citation network (the existing canvas view with the
  degree-aware layout), filterable by tag, project or search result, with
  candidates the library cites but lacks as hollow rings.
- **Tags and projects**: browse, rename, assign to a selection.
- **Reading**: the reading list by depth, and what is due for recall.
- **Ask**: questions answered by Claude (below).

**Writes** (tags, sharing, reading status) go through the `Library` API, so
the rules of §§ 4–8 hold. Each write is journalled and the library repo gets
one commit per quiet spell (debounced), not one per click.

**Ask.** Questions are answered by the researcher agent with the read-only
tools of § 13, streamed to the page with the evidence (chunk IDs, quotes)
as clickable links. Two back ends, configured in `library.toml`:

- `api`: the Anthropic API (`ANTHROPIC_API_KEY`), using the tool runner; the
  user pays per question.
- `claude-code`: the local Claude Code CLI in headless mode with the litnav
  MCP server, for users who have Claude Code; no API key needed.

Only text the sharing levels allow for `send_out` is ever given to the model;
`confidential` items are invisible to Ask. The page says, once, that a
question and the passages it retrieves are sent to Anthropic.

## 17. Decisions

Settled 2026-11-15:

- **Name** litnav. **Licence** MIT.
- **Collision rule** (§ 4): append the next content word.
- **Each user has their own library.** Shared corpora are out of scope for
  now, so reading and recall state stay inside the library.

Still open:

1. **Summaries without an API key**: let the curator skill write summaries
   through the user's Claude Code session, as well as the batch API.

## 18. Plan

1. Repo, packaging, `init`, `ref.bib` + `meta.toml`, keys, sharing levels, with the § 14
   tests.
2. Import (PDF, `.bib`, legacy converter) and metadata hygiene. Migrate the
   existing corpus; compare item by item with the old system.
3. Extraction with sections, chunks, index, hybrid search; the eval set; beat
   the old passage search on it.
4. MCP server and the four skills.
5. Web app, with Ask.
6. Reading, recall, quizzes, discovery, knowledge layer.
7. Department trial: install guide, a five-minute quickstart from an empty
   directory and from a Zotero export, feedback, fixes.
