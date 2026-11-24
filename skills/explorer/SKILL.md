---
name: explorer
description: Find papers the user does not have yet — works the library cites but lacks, citation-graph neighbours, recent work building on key papers — and keep the to-fetch list. Use when the user asks what they are missing, wants reading suggestions beyond the library, or wants papers fetched.
---

# Explorer: discovery outside the library

## Commands

| | |
|---|---|
| `litnav candidates [--min-cites N] [--add N]` | outside works cited by several library papers, with which papers cite them |
| `litnav explore <keys> [--add N]` | Semantic Scholar recommendations for seed papers |
| `litnav to-fetch [list\|add\|skip\|open N\|prune]` | the to-fetch list, `discovery/to-fetch.tsv` |
| `litnav field build` / `forward` / `embed` | the field store: works the library cites (backward) and works citing many library papers (forward), with public abstracts, embedded locally |
| `litnav field list [--kind K] [--set S]` | top field works; kinds research, review, dataset, model, project |
| `litnav field search "<q>"` (MCP `search_field`) | semantic search over field abstracts; library papers come back marked held |
| `litnav field map` | topic clusters, least-held first (`derived/field-map.md`): where the library is thin |
| `litnav field want\|skip\|note <ids>` | decisions, kept in `discovery/field.tsv`; `want` also adds to to-fetch |

Field works are abstract-only: say so, and never present one as read.

Results flagged `[maybe held: <key>]` may already be in the library under
another key or as a preprint: check before proposing them.

## Sources

- **The citation graph** (`litnav graph`, OpenAlex): `graph.json` in the
  index directory lists `candidates`, outside works cited by several library
  papers (`cited_by_library`), with DOI, title, year and first author. The
  web app's Graph view shows the library network.
- **OpenAlex** for reference lists and works citing a paper; **Semantic
  Scholar** for recommendations seeded from papers the user rates highly;
  **arXiv** for preprints; **Crossref** for metadata only. Google Scholar is
  off-limits (no API; scraping breaks its terms).
- Within the library: `related_papers`, `litnav similar <key>`.

## Method

1. Start from the user's question or seed papers. Prefer candidates cited by
   many library papers, and recent papers that cite several library papers.
2. Check each candidate is not already held (`list_papers` by author and
   year, or `litnav search` on its title) — a preprint or a different key may
   already cover it.
3. Present a short list: title, authors, year, DOI, why it came up (which
   library papers cite it or are cited by it). Rate interest from the
   abstract only if the user asks; never claim to have read a paper you only
   have metadata for.
4. Papers the user wants go on the to-fetch list (`--add N`, or
   `litnav to-fetch add <doi> --source <where it came up>`). Papers they
   don't want: `litnav to-fetch skip <doi>`, so they are not proposed again.

## Boundaries

- **Metadata only.** Send titles, DOIs or OpenAlex IDs to outside services;
  never text from the library, and nothing about `private` or `confidential`
  items.
- **Never download PDFs.** To fetch, run `litnav to-fetch open 5` (or
  `litnav fetch <DOIs>`), a handful at a time, so the user downloads into
  the inbox.
