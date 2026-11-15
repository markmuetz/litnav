# litnav

A local, file-based literature library for researchers and their agents.

Your papers live in a plain directory that is a git repo: one folder per
paper, keyed by citation key, with a normalised `ref.bib`, the extracted text
with its section structure, and your own notes. litnav imports PDFs and
verifies their metadata, keeps the bibliography clean, searches by keyword and
by meaning (locally), maps the citation network, keeps a reading list and
spaced-recall queue, and exposes all of it to Claude (or any MCP client)
through read-only tools and skills. A localhost web app lets you browse,
search and ask questions.

**Status: early development.** Design: [`docs/design.md`](docs/design.md).
Build plan: [`docs/plan.md`](docs/plan.md).

## Install (development)

```sh
uv tool install --editable '.[all]'
litnav init ~/Papers
```

## Licence

MIT. litnav stores *your* PDFs and the text extracted from them. Keep your
library repo private: pushing the text of licensed papers somewhere public is
redistribution.
