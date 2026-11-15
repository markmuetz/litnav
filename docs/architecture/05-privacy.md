---
title: "litnav architecture 5: Privacy and sharing"
subtitle: "What can leave your machine, and the gates that decide"
date: "2026-11-15 (v0.1.0)"
---

# Sharing levels

Every item has one sharing level in `meta.toml` (`sharing = "..."`; new
items get `default_sharing`, `licensed` unless you change it):

| Level | Meaning | Typical items |
|---|---|---|
| `open` | openly licensed (CC): anything goes | CC-BY papers (set automatically from OpenAlex unless you set the level yourself) |
| `licensed` | publisher-licensed: you may read it and use it with tools; don't redistribute the text | most journal articles |
| `private` | yours, not for others to see, but usable with models | your own unpublished notes-as-items, theses |
| `confidential` | never read by tools, never sent anywhere | co-authored drafts, manuscripts under review |

An unknown level (a typo in `meta.toml`) is treated as `confidential`.

# Purposes and the table

Code never checks a level directly; it states a **purpose** and asks
`sharing.allow(level, purpose)`:

![The table in sharing.py. send_out_strict is what send_out becomes when you answered no to sending licensed text to models.](diagrams/sharing.pdf){width=100%}

- `local`: you, on this machine (CLI output, the web app's browsing views).
- `lookup`: an item's metadata (DOI, title) sent to Crossref, OpenAlex,
  arXiv or Semantic Scholar.
- `send_out`: an item's text, summary or passages sent to a model.
- `send_out_strict`: the same, in a library with `licensed_to_models =
  false` (asked once by `litnav init`). `Library.purpose()` maps
  `send_out` to it; callers never pass it themselves.
- `serve_meta` / `serve_text`: serving to someone other than you (reserved for
  a future shared server; nothing uses them yet).

# The gates

![Every gate calls the one table.](diagrams/gates.pdf){width=95%}

| Gate | Where | Stops |
|---|---|---|
| `net.Client._check(purpose, items)` | the only HTTP client | any API request about an item that doesn't allow the purpose; raises `Refused` before connecting |
| `derived.outbound(lib, items)` | summaries, themes | any model call if any selected item doesn't allow `lib.purpose(SEND_OUT)`; re-checked per paper during long runs and at batch collection |
| `Library.items(purpose)`, `Item.allowed(purpose)` | graph, discovery seeds, themes inputs | confidential (and other disallowed) items never enter the list |
| `chunks.sharing` + `search` purpose filter | search, MCP tools, Ask | passages of disallowed items never returned; `embed.allowed_mask` does the same for vectors |
| `Tools(purpose=send_out)` | MCP server, Ask | text of disallowed items; metadata still allowed when only the strict rule applies (`meta_purpose`) |
| confidential import path | `importer` | `_inbox/_confidential/` files are never text-extracted or looked up |
| `extract` | `extract.py` | never extracts text for confidential items |

# What leaves the machine, by destination

| Destination | What is sent | When | Never sent |
|---|---|---|---|
| Crossref | a DOI; or a title query built from a PDF's opening lines | import | anything about confidential items |
| arXiv | an arXiv id | import | |
| OpenAlex | DOIs | `graph` | confidential DOIs |
| Semantic Scholar | the seed papers' DOIs | `explore` | confidential seeds (refused) |
| Anthropic API | paper text (summaries); summaries (themes); question, tool results (Ask) | only when you run those | confidential text; licensed text if `licensed_to_models = false` |
| `claude` CLI | same as the API, through your Claude Code login | same | same |
| Your browser | DOIs to open | `to-fetch open`, `fetch` | |

Everything else (embeddings, OCR, search, the web app) runs locally.

# Things that are your responsibility

- **The library repo holds licensed text** (`text.txt`, if `track_text =
  true`). Push it only to a private remote.
- **Notes are yours**: litnav never sends a note on its own. `get_note`
  returns your note on a paper to the model when asked, and refuses for a
  confidential item; but a note on paper A that *discusses* a confidential
  draft B is still a note on A. Keep comments on confidential drafts in the
  draft's own home.
- **A Claude Code session with Bash** can read any file you can. The MCP
  tools respect sharing; a general agent with shell access is governed by
  your Claude Code permissions and your CLAUDE.md, not by litnav.
