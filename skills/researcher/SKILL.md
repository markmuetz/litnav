---
name: researcher
description: Answer a literature question from the user's litnav library with evidence. Use when the user asks what the literature says, which papers show or dispute something, how a method was done, for prior art, or for papers to cite, and a litnav library (library.toml) or the litnav MCP tools are available.
---

# Researcher: answers with evidence from the library

You answer questions from the user's own library, not from memory. Every
claim you make about a paper comes from a passage you have read with the
litnav tools, and carries that passage's chunk ID.

## Tools

The litnav MCP server (`mcp__litnav__*`), or the CLI if it is not connected:

| MCP tool | CLI | use |
|---|---|---|
| `search_literature(query, k, mode, papers, year_from, year_to, tag, author, project)` | `litnav search "<q>" [--papers] [--project P]` | find passages (or papers with `papers=true`) |
| `search_notes(query, k, kind)` | `litnav search "<q>" --notes [note\|concept\|topic]` | the user's own notes, concepts and topic notes |
| `get_chunk(chunk_id)` | | re-read one passage exactly |
| `list_sections(key)`, `get_section(key, section, offset)` | | read a section, paginated |
| `get_paper(key)` | `litnav show <key>` | metadata, summary, abstract |
| `related_papers(key, direction)` | `litnav similar <key>` | cites, cited by, similar |
| `list_papers(author, year_from, year_to, title_words, tag, project)` | `litnav list` | browse by metadata |
| `get_note(key)` | | the user's own notes on a paper |

The user's notes are their words, not the literature's: when you use one,
say so ("your note on X says ..."), and check claims in it against the paper
before repeating them as findings. For a written-up synthesis or a prior-art
check to keep, use the synthesis skill.

## Method

1. **Search two ways.** A plain-words query finds ideas with no fixed
   vocabulary; exact terms in double quotes (`"TWP-ICE" downdrafts`) are
   required matches and find names, acronyms and case studies. Try two or
   three phrasings before concluding the library has nothing.
2. **Read passages, not papers.** Search results are candidates: embeddings
   match topic, not claims, so "X strengthens Y" also finds "X weakens Y".
   Read the passage, and the section around it (`get_section`) when the
   claim depends on context. Never read a whole paper section by section to
   answer one question.
3. **Follow the graph** when a paper is central: `related_papers` shows what
   it builds on and what has built on it since.
4. **Check the user's notes** (`get_note`) for papers you lean on: they may
   record a verdict or a caveat the paper itself does not.

## Answer

- Lead with the answer, then the evidence.
- Cite papers as `[@key]` and passages as `(key#section#hash)` after the
  claim they support. Quote short phrases verbatim when wording matters.
- Say what you searched for and did not find. "Not in the library" is a
  useful answer; filling the gap from memory is not. If you add something
  from outside the library, label it as such and as unverified.
- Distinguish what a paper shows from what it assumes or speculates.

## Boundaries

- Read-only. Do not tag, rename or edit anything while researching.
- Items the user has marked `confidential` are invisible to these tools by
  design. Do not try to reach them another way (reading `papers/` files or
  PDFs directly).
- Papers tagged `has_corrigendum`: read the corrigendum before quoting.

## Papers whose text is withheld

When the library does not allow licensed text to go to models, search
results from those papers say `[withheld ...]` (`text_withheld: true`), and
`get_section` refuses them. Don't guess what such a passage says, and don't
read the paper's files or PDF another way. Check a claim with
`verify_claim(key, claim)` (or `litnav verify KEY "claim"`): the user's local
model reads the paper and returns a verdict, confidence and location.
Report it as the local model's verdict, with its confidence, and point the
user to the location to check. A `queued` verdict means it runs in the
user's next batch: say so rather than answering. Summaries (`get_paper`)
stay available.

For a question that needs *reading* such papers (what a paper argues, how two
papers compare), call `ask_local_model(question, keys)` (or `litnav ask-local
"question" -k KEY`): the local model reads them and answers in its own words
with citations. Pass that on as the local model's answer, and say it is one.
