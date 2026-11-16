---
name: synthesis
description: Write or update a topic note in topics/: a literature synthesis on a question, a prior-art (novelty) check for a research idea, or a reading list on a theme. Searches the library first, then outside sources by metadata. Use when the user asks for a write-up, review, overview or "what is known about X" to keep, asks whether an idea has been done before, or asks to update an existing topic note.
---

# Synthesis: topic notes and prior-art checks

A topic note is a file in `topics/<slug>.md` that answers one question from
the literature and records how the answer was found. There are three kinds:

- **synthesis**: what the literature says on a question, and how its parts fit;
- **prior-art check**: has an idea been done, published or scooped?
- **reading list**: the key papers on a theme, ranked, with why each matters.

## Commands

| | |
|---|---|
| `search_literature`, `get_section`, `get_chunk` (MCP) or `litnav search` | find and read passages in the library |
| `related_papers`, `litnav similar <key>` | citation-graph neighbours of a central paper |
| `list_papers`, `get_note` | metadata and the user's notes on a paper |
| `search_notes` (MCP) or `litnav search --notes topic` | what the user has already written: topic notes, concepts, notes on papers |
| `litnav candidates`, `litnav explore <keys>` | outside works cited by the library, recommendations |
| `litnav to-fetch add <doi> --source topics/<slug>.md` | queue an outside paper the user wants |
| `litnav check-refs` | check that every `[@key]` in the note resolves |

Outside the library: OpenAlex (search, references, works citing a paper),
Crossref (metadata), arXiv (preprints), and the web for programme or dataset
pages. Never Google Scholar.

## Method

1. **Pin down the question.** Write it as one or two sentences. For a
   prior-art check, get the novelty claims from the user in their own words
   and test those; ask if they are not stated. Search the user's notes first
   (`search_notes`, or `litnav search --notes`): if a topic note already
   covers the question, update it rather than start another.
2. **Library first.** Search two or three ways (plain words, and quoted
   names, acronyms and case studies), read the matching sections, and follow
   the graph from the central papers (`related_papers`). Check the user's
   notes on the papers you lean on.
3. **Then outside, by metadata.** Query OpenAlex with several phrasings of
   the question, plus forward citations and reference lists of the central
   papers. Dedupe against the library (`list_papers`; it may hold a preprint
   or another key). Send only search terms, titles, DOIs or IDs; never library
   text, and nothing about private or confidential items.
4. **Read at the right depth, and say which.** Library papers: passages,
   with chunk IDs. Outside papers: usually the abstract only; mark these.
   Never claim to have read what you have metadata for.
5. **Write the note** (layout below), then run `litnav check-refs`.
6. **Offer the gaps.** List outside papers that would change the answer;
   add to the to-fetch list only those the user picks.

## Layout

```markdown
# <Title as a question or a topic>

Built <YYYY-MM-DD> from <library search | library, then OpenAlex/web>,
<for which project or at whose request>. Question: <the question>.

**Short answer.** <Three to six sentences: the answer, its main condition,
and the strongest evidence. For a prior-art check, the status: "nothing
found", "partly done by [@key]", or "done".>

**Provenance.** <Whose idea it is; which parts are Claude's; what was
checked against full text. Papers marked † were read at abstract level
only.>

Related: <other topic notes, by filename>.

## <Sections that follow the argument, not the papers>

## Search record
<Queries and sources tried, dates, counts (found, relevant, read),
and what was not found.>

## Candidates not in the library
| work | id | why it matters |
```

## Rules

- **Lead with the short answer.** Sections then give the evidence, organised
  by the argument; a paper-by-paper list belongs only in a reading list.
- **Every claim about a paper is cited** as `[@key]` for library items, with
  a chunk ID or section number where the wording matters; quote short
  phrases verbatim. Outside works are cited by DOI or arXiv ID and marked †
  when read only at abstract level.
- **Separate the layers**: what a paper shows, what it assumes or
  speculates, and your own synthesis across papers. Label your synthesis as
  yours. Numbers not checked against the text are marked as such.
- **"Nothing found" is a result.** State what was searched, where and when,
  so the check can be repeated. Never fill a gap from memory; if you add
  something from your own knowledge, label it unverified.
- **For a prior-art check**: a co-author's own earlier work is not a scoop
  risk, so check author lists. Close preprints count. Say how close each
  near-miss is, and what would separate the user's idea from it.
- **The note's judgements are the user's.** Record the user's verdicts in
  their words when they give them; don't write conclusions for them on what
  their idea is worth. Where a library or project rule limits what you may
  interpret (for example, a journal that forbids generative AI for
  interpretation), report facts and open questions only.
- **Updating a note**: keep the original date, and add a dated line saying
  what changed. Don't silently rewrite earlier findings; mark them
  superseded.
- **Confidential items** are invisible to the tools; never reach them
  another way, and never cite their content in a note.
- **A note written for confidential work** (a manuscript being refereed,
  a student's work) cites nothing in the library that marks it as such, so
  give it front matter `sharing: confidential`, which keeps it off every
  model-facing tool. A note that cites a confidential item is withheld
  automatically.
