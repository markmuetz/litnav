# litnav architecture

Six documents, from the map down to each module. PDFs (with the diagrams) are
in `pdf/`; rebuild them with `make` here (needs Graphviz, pandoc and xelatex).

| # | Markdown | PDF | Covers |
|---|---|---|---|
| 1 | [Overview](01-overview.md) | [pdf](pdf/01-overview.pdf) | the map, layers, principles, common use cases, one paper's journey |
| 2 | [Data and storage](02-data.md) | [pdf](pdf/02-data.pdf) | files and formats, the index schema, outside data sources, who writes what |
| 3 | [Data flows](03-flows.md) | [pdf](pdf/03-flows.pdf) | import, index and search, Ask, summaries and themes, graph and discovery, reading and recall |
| 4 | [Interfaces](04-interfaces.md) | [pdf](pdf/04-interfaces.pdf) | CLI, MCP tools, the web app's HTTP API and security, the Claude Code plugin |
| 5 | [Privacy and sharing](05-privacy.md) | [pdf](pdf/05-privacy.pdf) | sharing levels, purposes, the gates, what leaves the machine |
| 6 | [Module reference](06-modules.md) | [pdf](pdf/06-modules.pdf) | every module, the import graph (generated from the code) |

Diagrams are Graphviz sources in `diagrams/*.dot`; `diagrams/gen_modules.py`
and `gen_modules_md.py` regenerate the module graph and document 6.
The design rationale is in [../design.md](../design.md); the plan and
history in [../plan.md](../plan.md).
