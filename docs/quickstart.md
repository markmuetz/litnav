# litnav quick start

litnav keeps your papers in a plain folder that is also a git repository: one
folder per paper, with clean BibTeX, the paper's text, a summary and your
notes. You search it by keyword or by meaning, browse it in a web page, and
ask Claude questions about it.

## 1. Install

You need Python 3.11–3.13 and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install 'litnav[all] @ git+https://github.com/markmuetz/litnav'
litnav doctor        # which optional tools are present
```

For the best text from PDFs install poppler (`brew install poppler`, or
`apt install poppler-utils`); for scanned PDFs, tesseract too.

## 2. Make a library

```sh
litnav init ~/Papers
cd ~/Papers
```

`init` asks one question: may the text of papers you hold under a publisher's
licence be sent to a model (for summaries and questions)? Your answer is kept
in `library.toml` as `licensed_to_models`; change it there at any time.

Keep the library's git repository **private**: it holds your papers' text.

## 3. Add papers

### From PDFs

Save PDFs into `pdfs/_inbox/`, with whatever names the publisher gave them. A
subfolder becomes a tag: `pdfs/_inbox/thesis/` tags its papers `thesis`.

```sh
litnav import            # a dry run: shows the citation key it would give each PDF
litnav import --apply    # do it (or --from-plan to apply exactly what the dry run showed)
```

litnav finds each paper's DOI (or arXiv id, or a title match it verifies
against the text), fetches its metadata from Crossref, names it
`<author><year><word>` (e.g. `smith2021cold`), and files the PDF as
`pdfs/<key>.pdf`. Anything it is not sure of stays in the inbox for you.

Drafts you co-author, or anything else that must never leave your computer:
put them in `pdfs/_inbox/_confidential/`. They are never read, looked up or
sent anywhere.

### From Zotero, JabRef or Mendeley

Export your collection as BibTeX **with files** (Zotero: right-click the
collection → Export Collection → BibTeX, tick "Export Files"), then:

```sh
litnav import-bib ~/Downloads/Exported\ Items/Exported\ Items.bib            # dry run
litnav import-bib ~/Downloads/Exported\ Items/Exported\ Items.bib --apply
```

## 4. Search

```sh
litnav index                                # text, sections, and local embeddings
litnav search "cold pools organise convection"
litnav search '"TWP-ICE" downdrafts'        # "quoted" terms must appear
litnav search "squall line orientation" --papers
litnav similar smith2021cold
```

Embeddings run on your computer; nothing is sent anywhere.

## 5. Browse

```sh
litnav serve
```

opens a page in your browser (if you close it, use the link it prints: the
`?key=` part is what lets you in) with your library, search, each paper (summary, notes,
sections, what it cites and what cites it), the citation network, tags, your
reading list, and **Ask**, where Claude answers questions from your papers
with links to the passages it used.

Ask, summaries and the Claude Code skills use either an Anthropic API key
(`ANTHROPIC_API_KEY`) or, without one, your Claude Code login.

## 6. Summaries, the citation network and discovery

```sh
litnav summarize          # a summary per paper (Haiku; Batch API, or Claude Code)
litnav graph              # citation network from OpenAlex
litnav candidates         # papers your library cites often but you don't have
litnav explore smith2021cold        # Semantic Scholar recommendations
litnav to-fetch                     # the list of papers to get
litnav to-fetch open 5              # opens 5 of them in your browser to download
litnav themes                       # a report on the themes across your library
```

litnav never downloads PDFs itself: it opens the publisher's page and you
save the PDF into the inbox.

## 7. Reading and recall

```sh
litnav read add smith2021cold --depth read     # deep, read, skim, scan
litnav read done smith2021cold --depth read    # adds a recall card
litnav due                                     # what is due today
litnav recall smith2021cold easy               # forgot, hard, medium, easy
```

Write notes in `notes/<key>.md`; cite papers as `[@key]`.

To consolidate a paper you have read, talk it through rather than take a
quiz: in Claude Code, `/litnav:discuss <key>`. You explain it in your own
words first; Claude asks follow-up questions, corrects you only from the
paper itself (quoting the passage), and logs what was solid and what was
shaky in `quizzes/<key>.md`.

## 8. Claude Code

```sh
claude plugin marketplace add markmuetz/litnav
claude plugin install litnav@litnav
```

Then run `claude` inside your library. The skills: **researcher** (answers
with evidence), **reader** (reading list, quizzes, recall), **curator**
(imports and metadata) and **explorer** (finding papers you don't have).

## Sharing levels

Each paper has one, shown on its page and set with `litnav share`:

| level | sent to a model for you | shown to others |
|---|---|---|
| `open` (Creative Commons; set from OpenAlex) | yes | full text |
| `licensed` (the default) | if you said yes at `init` | metadata, abstract, summary |
| `private` | yes | nothing |
| `confidential` | never; not even looked up | nothing |

## Commit your library

litnav writes plain files; commit them as you would any repository:

```sh
git add -A && git commit -m "Import papers."
```

The web app commits its own edits (tags, reading list) for you.
