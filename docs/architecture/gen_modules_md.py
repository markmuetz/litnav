"""Generate 06-modules.md from the source tree (run from the repo root)."""
import ast
from pathlib import Path

SRC = Path("src/litnav")
ROLE = {
 "cli": "Entry point: builds the argparse tree from commands/*.",
 "config": "library.toml defaults, loading, the local.toml overlay.",
 "library": "Item and Library: the file model, resolve/aliases, items(purpose).",
 "meta": "meta.toml read/write (stable field order, empty tables dropped).",
 "bib": "BibTeX parse/write and house-style normalisation (authority files).",
 "keys": "Citation-key derivation, folding, stop words, collision handling.",
 "sharing": "Sharing levels, purposes, the allow() table.",
 "util": "Atomic writes and small helpers.",
 "net": "The only HTTP client: sharing check, cache, rate limit, retries.",
 "sources.crossref": "Crossref works API: by DOI, title query, BibTeX.",
 "sources.arxiv": "arXiv API: metadata by id.",
 "sources.openalex": "OpenAlex works: by DOI, reference lists.",
 "extract": "pdftotext and OCR; text.txt for items.",
 "identify": "The identification ladder and title verification.",
 "importer": "Import plan/apply, duplicate catalogue, inbox handling.",
 "importers.bibtex": "Import from a Zotero/JabRef/Mendeley .bib with file fields.",
 "importers.legacy": "Convert a litman data directory.",
 "init": "litnav init: create a library, ask the licensed-text question.",
 "sections": "Section detection in extracted text.",
 "chunks": "Section-aware chunking with stable chunk IDs.",
 "index": "The SQLite index: schema, incremental update, signatures.",
 "embed": "Local embeddings (fastembed), vector store, matrices.",
 "search": "Keyword, semantic and hybrid search with filters and purpose.",
 "tools": "Read-only tools shared by MCP and Ask.",
 "mcp_server": "FastMCP stdio server over tools.",
 "ask": "Ask: Anthropic API loop or claude -p over the MCP server; events.",
 "derived": "Summaries and themes: selection, outbound gate, API batches, Claude Code.",
 "graph": "Citation graph and candidates from OpenAlex.",
 "discovery": "Holdings, candidates, Semantic Scholar explore, to-fetch list.",
 "reading": "Reading list and Leitner recall scheduling.",
 "quizzes": "Quiz and discussion logs.",
 "refs": "[@key] parsing, backlinks, check/convert refs.",
 "rename": "Rename a key everywhere, keeping an alias.",
 "projects": "Project membership lists.",
 "evaluate": "Search evaluation: recall@10, MRR, compare runs.",
 "web.app": "Starlette web app: guard, API routes, git commits of writes.",
}

def name(p):
    return ".".join(p.relative_to(SRC).with_suffix("").parts)

rows = []
for p in sorted(SRC.rglob("*.py")):
    if p.name == "__init__.py" or "commands" in p.parts:
        continue
    m = name(p)
    t = ast.parse(p.read_text())
    fns = [n.name for n in t.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and not n.name.startswith("_")]
    lines = len(p.read_text().splitlines())
    rows.append((m, lines, ROLE.get(m, ""), ", ".join(f"`{f}`" for f in fns[:9]) + (" ..." if len(fns) > 9 else "")))

out = ["---", 'title: "litnav architecture 6: Module reference"',
       'subtitle: "Every source file, what it does, and how they depend on each other"',
       'date: "2026-11-15 (v0.1.0)"', "---", "",
       "# The import graph", "",
       "Generated from the code (`docs/architecture/diagrams/gen_modules.py`). Green: interfaces; grey: workflows and tools; blue: index and search; purple: network; yellow: the library model. Arrows point from importer to imported. `commands/*` (the CLI layer) is left out: each command module imports the core module of the same name.", "",
       "![Internal imports between modules.](diagrams/modules.pdf){width=100%}", "",
       "Read it right to left for the foundations: `util`, `sharing`, `keys`, `config`, `bib`, `meta` and `library` depend on almost nothing; `net` depends only on `sharing`; everything that talks to the outside goes through `net`.", "",
       "# Modules", "",
       "| Module | Lines | Role | Public names |", "|---|---:|---|---|"]
for m, n, r, f in rows:
    out.append(f"| `{m}` | {n} | {r} | {f} |")
out += ["", "# The CLI layer (`commands/`)", "",
        "| File | Commands |", "|---|---|"]
for p in sorted((SRC / "commands").glob("*.py")):
    if p.name == "__init__.py":
        continue
    d = ast.get_docstring(ast.parse(p.read_text())) or ""
    out.append(f"| `commands/{p.name}` | {d.splitlines()[0] if d else ''} |")
out += ["", "# Web front end (`web/static/`)", "",
        "| File | Role |", "|---|---|",
        "| `index.html` | one page; the session token is injected as `__TOKEN__` |",
        "| `app.js` | views (Library, Search, Paper, Tags, Reading, Discovery, Ask), hash routing, safe Markdown rendering, SSE for Ask |",
        "| `graph.js` | force-directed citation graph on a canvas; colour by decade/tag; candidate rings |",
        "| `style.css` | dark theme |",
        "", "# Tests", "",
        "`tests/` runs with `pytest` (122 tests, ~4 s): library and keys, bib normalisation, import and identification with recorded API responses, sections and chunks, index and search, the sharing gates (net refusal, outbound, MCP tools, search filters), derived routes with a fake `claude`, the web app's guard and endpoints, reading and recall dates, discovery, quizzes. CI runs them on Ubuntu, macOS and Windows with Python 3.11-3.13."]
Path("docs/architecture/06-modules.md").write_text("\n".join(out) + "\n")
print(len(rows))
