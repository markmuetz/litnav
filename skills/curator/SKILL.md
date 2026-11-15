---
name: curator
description: Keep a litnav library clean — import PDFs from the inbox, verify metadata and citation keys, normalise BibTeX, find duplicates, handle preprints, corrigenda and co-authored drafts, rename keys. Use when the user wants to import papers, fix a bib entry or key, check the library, or tidy metadata.
---

# Curator: imports, keys and metadata

## Commands

| | |
|---|---|
| `litnav import [paths] [--apply]` | import PDFs (default: everything in `<pdf_dir>/_inbox/`); dry run unless `--apply` |
| `litnav import-bib <file.bib>` | import from Zotero/JabRef/Mendeley exports |
| `litnav check` | keys, implausible years, missing bibs and DOIs |
| `litnav normalise [keys]` | bring `ref.bib` into house style (idempotent) |
| `litnav rename <old> <new>` | rename; the old key stays as an alias, `[@old]` still resolves |
| `litnav tag add\|rm <tag> <keys>`, `litnav share <level> <keys>` | tags, sharing |
| `litnav fetch <keys or DOIs>` | open DOIs in the browser for the user to download |
| `litnav extract`, `litnav index` | text for PDFs that lack it; update the search index |

## Rules

- **Dry run first.** Show the plan from `litnav import` and let the user
  confirm before `--apply`. Unverified matches are reported, never applied.
- **Keys**: `<surname><year><firstcontentword>`, surname folded to ASCII,
  year of the issue (not online-first), leading function words skipped.
  Suffixes are part of the convention: a digit for Part I/II, `_preprint`,
  `_supplement`, `_corrigendum`, `_draft` and similar markers. A clash
  appends the title's next content word. Never use `-N` for duplicates.
- **Duplicates**: the importer checks file hash, DOI and title. When the same
  paper appears under two keys, the user decides which survives.
- **Preprints** are kept beside their published version, never replaced:
  key `_preprint`, relation recorded in `meta.toml` `[relations]`.
- **Corrigenda** are their own item, `<parent>_corrigendum`, tagged and linked.
- **Fetching is manual**: open DOIs in the user's browser; never download a
  PDF yourself.
- Run `litnav normalise` after anything that writes a bib.

## Confidential items

Co-authored drafts and anything else marked `confidential` (or dropped into
`_inbox/_confidential/`): never read the PDF, text, notes or comments, and
never send anything about them to a model or an outside service — no text
extraction, summary, OCR, or DOI/title lookup. Handle only the key, title,
authors, where the canonical version lives, and the version history. litnav
enforces this in its tools; don't work around it with direct file access.
