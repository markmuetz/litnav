"""mcp, serve."""
from __future__ import annotations


def add(sub):
    p = sub.add_parser("mcp", help="run the MCP server on stdio (for Claude Code and other MCP clients)")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("serve", help="run the web app on localhost")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_serve)


def cmd_mcp(a):
    from ..mcp_server import main
    main(a.library)


def cmd_serve(a):
    from ..web.app import serve
    serve(a.library, port=a.port, browser=not a.no_browser)
