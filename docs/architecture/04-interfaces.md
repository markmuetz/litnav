---
title: "litnav architecture 4: Interfaces"
subtitle: "CLI, MCP server, web app and its HTTP API, Claude Code plugin"
date: "2026-11-15 (v0.1.0)"
---

All four interfaces are thin: they parse input, call the core, and format
output. None of them holds logic the others lack, except the web app's
per-write git commits.

| Interface | Process | Who uses it | Reads | Writes | Purpose (sharing) |
|---|---|---|---|---|---|
| CLI | `litnav <cmd>` | you; Claude Code via Bash | everything | everything | per command (doc 5) |
| MCP server | `litnav mcp`, stdio, spawned by Claude Code | Claude | index + files via `Tools` | **nothing** | `send_out` |
| Web app | `litnav serve`, Starlette + uvicorn on 127.0.0.1 | you in a browser | index + files | tags, sharing, reading list, to-fetch | `local` for browsing; `send_out` inside Ask |
| Plugin | skills + `/litnav:discuss` + `.mcp.json` | Claude Code | via MCP and CLI | via CLI, with your approval | as the tools they call |

# 4.1 CLI

`cli.py` builds an `argparse` tree; each `commands/*.py` registers its
subcommands and a `func`. `--library/-L` picks the library.

| Group (`commands/`) | Commands |
|---|---|
| core | `init`, `list`, `show`, `tag`, `share`, `bib`, `check`, `normalise` |
| importing | `import`, `import-bib`, `import-legacy`, `fetch` |
| text | `extract`, `index`, `doctor` |
| searching | `search`, `similar`, `eval` |
| refs | `check-refs`, `convert-refs`, `backlinks`, `rename`, `graph` |
| reading_cmds | `read add/done/list`, `due`, `recall`, `quiz list/show/start` |
| derived_cmds | `summarize`, `themes` |
| discovery_cmds | `candidates`, `explore`, `to-fetch list/add/skip/open/prune` |
| web_cmds | `mcp`, `serve` |

Conventions: anything that changes many files is a **dry run unless
`--apply`** (import, rename, convert-refs); anything that costs money or
plan usage **says so and asks** (`summarize` via Claude Code over a
threshold; `--yes` skips); `--json` where a script might want it.

# 4.2 MCP server (Claude Code's view of the library)

`mcp_server.build()` registers the nine methods of `tools.Tools` on a
FastMCP server with the descriptions in `DESCRIPTIONS` and the server
`INSTRUCTIONS` (cite `[@key]` + chunk ID, quote verbatim, results are
candidates, quote exact names). All tools are **read-only**.

| Tool | Arguments | Returns |
|---|---|---|
| `search_literature` | `query`, `k`, `mode` (hybrid/keyword/semantic), `papers`, `year_from`, `year_to`, `tag`, `author` | hits: chunk id, key, citation, section, verbatim text, score |
| `get_chunk` | `chunk_id` | one passage |
| `list_sections` | `key` | slug, title, size per section |
| `get_section` | `key`, `section`, `offset` | section text, paginated (`next_offset`) |
| `get_paper` | `key` | metadata, abstract, summary |
| `related_papers` | `key`, `direction` (cites/cited_by/similar/both), `k` | neighbours from `edges` and vectors |
| `list_papers` | `author`, `year_from`, `year_to`, `title_words`, `tag`, `k` | keys + citations |
| `reading_due` | none | due cards, papers being read |
| `get_note` | `key` | your note and which notes cite the paper |

Every tool runs with purpose `send_out` (or `send_out_strict`): confidential
items do not exist as far as the model can tell, and text of items that
don't allow sending is withheld while metadata may still be shown.

How Claude Code finds it: the plugin's `.mcp.json` runs `litnav mcp`; the
library is found from the working directory or `$LITNAV_LIBRARY`.

# 4.3 Web app and its HTTP API

`web/app.py` builds a Starlette app; `web/static/` holds one page
(`index.html`), `app.js` (views, Markdown rendering, Ask streaming) and
`graph.js` (force layout on a canvas). Views: Library, Search, Paper, Graph,
Tags, Reading, Discovery, Ask.

**Security model** (it serves the full text and PDFs of licensed papers):

1. binds to `127.0.0.1` only;
2. `Host` must be `127.0.0.1:<port>` or `localhost:<port>` (stops DNS
   rebinding);
3. **a launch key**: `litnav serve` prints and opens
   `http://127.0.0.1:<port>/?key=<random>`. Only that link gets the page.
   On a shared machine (e.g. a JASMIN sci node) other users can also reach
   127.0.0.1, so the port alone proves nothing;
4. the link sets a random per-run session cookie (`HttpOnly`,
   `SameSite=Strict`, named `litnav<port>`) and redirects to `/`; the
   cookie is required for the page and on every `/api` and `/pdf` request;
5. every `POST` also needs the token in `X-Litnav-Token` and our own `Origin`;
   no CORS headers are sent;
6. a strict Content-Security-Policy (`default-src 'self'`); the page builds
   DOM with `textContent` and escapes before rendering Markdown; every
   link shows its full address, so an answer cannot hide data in a URL.

| Method + path | Does | Notes |
|---|---|---|
| `GET /?key=K` | checks the launch key, sets the session cookie, redirects to `/` | 403 without it |
| `GET /` | the page, with the session token | needs the cookie |
| `GET /api/items` | list/filter items (`q`, `tag`, `project`) | from `items` |
| `GET /api/item/{key}` | one item: metadata, summary, sections, note, backlinks, edges | |
| `GET /api/item/{key}/section/{slug}` | a section's text | |
| `GET /api/item/{key}/similar` | nearest papers by vector | |
| `POST /api/item/{key}/tags` | add/remove tags | git commit |
| `POST /api/item/{key}/sharing` | set sharing level | git commit |
| `GET /api/search` | passages or papers | `q`, `mode`, `papers`, `k`, filters |
| `GET /api/graph` | `graph.json` nodes and links | |
| `GET /api/tags` | tag counts | |
| `GET`/`POST /api/reading` | reading list; add/done/status | git commit |
| `GET /api/bib` | BibTeX for `keys=` | |
| `GET /api/ask` | which backend is available | |
| `POST /api/ask` | stream an answer (server-sent events) | doc 3.3 |
| `GET /api/candidates` | discovery candidates (`min`) | |
| `GET`/`POST /api/to-fetch` | the to-fetch list; add/skip/opened | git commit |
| `GET /pdf/{key}` | the PDF, inline | key resolved through the library, never a path |
| `/static/*` | JS and CSS | |

**Writes and git.** `Git` (in `app.py`) commits only the paths a write
touched, under a lock, and does nothing while a merge or rebase is in
progress.

# 4.4 The Claude Code plugin

The repository is also a Claude Code plugin (`.claude-plugin/plugin.json`,
`marketplace.json`): it brings the MCP server (`.mcp.json`), four skills
and one command.

| Part | When it is used | What it adds |
|---|---|---|
| `skills/researcher` | literature questions, prior art, papers to cite | search strategy (quoted terms, hybrid, then read sections), evidence rules, answer format |
| `skills/reader` | reading list, what is due, quizzes, recall, **discuss** | depths, recall grading on the gist, quiz rules, the discussion protocol |
| `skills/curator` | importing, fixing keys and bibs, duplicates, preprints, drafts | the import ladder, key convention, confidential handling |
| `skills/explorer` | what am I missing, reading beyond the library | candidates, explore, to-fetch; never download PDFs |
| `commands/discuss.md` | `/litnav:discuss <key>` | runs the reader skill's discussion protocol |

Skills call MCP tools for reading and the CLI (through Bash) for anything
that writes, so writes are visible to you and go through the same code as
when you type them.
