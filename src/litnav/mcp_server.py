"""litnav MCP server (stdio): read-only tools over the library.

    litnav mcp [--library DIR]

Run by Claude Code (see .mcp.json / the plugin). Text goes to the model, so
tools run with purpose `send_out`: confidential items are invisible.
"""
from __future__ import annotations

from .library import Library
from .tools import Tools

INSTRUCTIONS = """\
Tools over the user's literature library (litnav). Cite papers as [@key] and
passages by chunk id (e.g. smith2021cold#4-2#a1b2c3d4), quoting short verbatim
extracts so claims can be checked. Search results are candidates to read, not
answers: embeddings match topic, not claims, so check direction and negation in
the passage itself. Put exact names, acronyms and case studies in double quotes
(they become required). Use list_papers to find a key from an author or year,
list_sections + get_section to read a specific part of one paper, and
related_papers for the citation network, and search_notes for what the user
has already written (topic notes, concepts, notes on papers). Never ask for
whole papers."""

DESCRIPTIONS = {
    "search_literature": "Search passages (default) or papers (papers=true). Hybrid keyword + "
                         "semantic; \"quoted phrases\" are required. Filters: year_from, year_to, "
                         "tag, author, project. Returns chunk ids, citations, section titles, "
                         "verbatim text.",
    "search_notes": "Search the user's own notes, concept notes and topic notes (kind = note, "
                    "concept or topic). Returns passage ids (path#section#hash), the note's path "
                    "and title, the paper a note is about, and verbatim text. Notes are the "
                    "user's words: quote them as theirs, not as the literature.",
    "get_chunk": "Fetch one passage by chunk id.",
    "list_sections": "List a paper's detected sections (slug, title, size).",
    "get_section": "Read one section of a paper by slug, paginated (offset/next_offset).",
    "get_paper": "Metadata, summary and abstract of one paper.",
    "related_papers": "Citation neighbours: direction = cites, cited_by, similar or both.",
    "list_papers": "Find papers by author, year range, title words, tag or project (to get keys).",
    "reading_due": "Recall cards due today and papers being read.",
    "get_note": "The user's own notes on a paper and which notes cite it.",
}


def build(lib, purpose="send_out"):
    from mcp.server.mcpserver import MCPServer
    from . import __version__
    from .tools import TOOL_NAMES
    t = Tools(lib, purpose=purpose)
    server = MCPServer("litnav", instructions=INSTRUCTIONS, version=__version__)
    for name in TOOL_NAMES:
        server.add_tool(getattr(t, name), name=name, description=DESCRIPTIONS[name])
    return server


def main(library=None):
    lib = Library.find(library)
    build(lib).run("stdio")
