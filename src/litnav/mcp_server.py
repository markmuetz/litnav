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
whole papers. Some papers' text may not go to a model (licensed): their
search results say where a match is but withhold the text. Don't guess what
such a passage says; check claims with verify_claim, which asks the user's
local model and reports a verdict, location and confidence. For a question
that needs reading such papers (what a paper argues, how two papers differ),
use ask_local_model: the local model reads them and answers in its own words.
Pass its answer on as the local model's, with its citations. search_field
looks beyond the library: works around it with public abstracts only."""

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
    "verify_claim": "Check one claim (content or bibliographic: volume, pages, article number) "
                    "against one paper with the user's local model. Returns verdict (supported, "
                    "partly_supported, contradicted, not_found), confidence, location (section, "
                    "page) and a short explanation, not the paper's text. A local model can be "
                    "wrong: report the confidence and location so the user can check. If no local "
                    "model is running the check is queued (verdict 'queued').",
    "search_field": "Semantic search over the field around the library: works the library cites "
                    "or that cite it, with their public abstracts (OpenAlex), plus the library's "
                    "own papers marked 'held'. For what exists beyond the library: candidates, "
                    "gaps, prior art. Field results are abstract-only and not held: say so, and "
                    "never present an abstract as having read the paper. `kind` filters to "
                    "research, review, dataset, model or project.",
    "ask_local_model": "Ask the user's local model a question that needs papers whose text you may "
                       "not read (licensed). It searches and reads the library and answers in its "
                       "own words with [@key] citations (optionally only from `keys`). Slower than "
                       "the other tools (a minute or more). A local model can be wrong: say the "
                       "answer is the local model's, and check its citations' metadata. "
                       "`profile` picks another endpoint the user has set up (e.g. a bigger model "
                       "on a cluster) when they ask for it.",
}


LOCAL_INSTRUCTIONS = """\
Tools over the user's literature library (litnav), for a model running on the
user's own machine: you may read the full text of licensed papers. Cite papers
as [@key] and passages by chunk id. Search first, then read the sections that
matter (list_sections + get_section). Answer in your own words: quote at most
a short phrase, never whole sentences or paragraphs."""

LOCAL_TOOLS_EXCLUDED = {"verify_claim", "ask_local_model"}       # it would call the local model from inside itself


def build(lib, purpose="send_out", *, local_model=False, access_log=None):
    from mcp.server.mcpserver import MCPServer
    from . import __version__
    from . import sharing as S
    from .tools import TOOL_NAMES
    if local_model:
        purpose = S.LOCAL_MODEL
    t = Tools(lib, purpose=purpose, access_log=access_log)
    server = MCPServer("litnav", instructions=LOCAL_INSTRUCTIONS if local_model else INSTRUCTIONS,
                       version=__version__)
    for name in TOOL_NAMES:
        if local_model and name in LOCAL_TOOLS_EXCLUDED:
            continue
        server.add_tool(getattr(t, name), name=name, description=DESCRIPTIONS[name])
    return server


class NotLocal(RuntimeError):
    pass


def check_local_launch(env) -> None:
    """`--local-model` serves licensed text, so it must only ever be wired to a
    model the owner runs. litnav ask-local starts OpenCode (with a local model)
    and sets LITNAV_LOCAL_AGENT=1; an agent such as Claude Code never does, and
    its shells carry CLAUDECODE=1."""
    if env.get("LITNAV_LOCAL_AGENT") != "1" or env.get("CLAUDECODE"):
        raise NotLocal("`litnav mcp --local-model` serves licensed paper text and only runs under "
                       "`litnav ask-local` (a local model). For any other client use `litnav mcp`.")


def main(library=None, *, local_model=False):
    import os
    lib = Library.find(library)
    log = None
    if local_model:
        check_local_launch(os.environ)
        log = os.environ.get("LITNAV_ACCESS_LOG") or None
    build(lib, local_model=local_model, access_log=log).run("stdio")
