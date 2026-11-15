"""Generate modules.dot: litnav's internal import graph (run from the repo root)."""
import ast
from pathlib import Path

SRC = Path("src/litnav")
GROUP = {"cli": "if", "mcp_server": "if", "web.app": "if", "ask": "if",
         "importer": "wf", "identify": "wf", "extract": "wf", "derived": "wf", "graph": "wf", "discovery": "wf",
         "reading": "wf", "quizzes": "wf", "rename": "wf", "evaluate": "wf", "init": "wf", "projects": "wf",
         "refs": "wf", "importers.bibtex": "wf", "importers.legacy": "wf",
         "tools": "rd", "search": "ix", "index": "ix", "sections": "ix", "chunks": "ix", "embed": "ix",
         "net": "net", "sources.crossref": "net", "sources.arxiv": "net", "sources.openalex": "net"}
COL = {"if": "#DFF3E3", "wf": "#EEEEEE", "rd": "#EEEEEE", "ix": "#DCEBFA", "net": "#F3E5F5", "core": "#FFF4D6"}

def modname(p):
    return ".".join(p.relative_to(SRC).with_suffix("").parts)

mods = {modname(p): p for p in SRC.rglob("*.py") if p.name != "__init__.py" and "commands" not in p.parts}
edges = set()
for m, p in mods.items():
    pkg = m.rsplit(".", 1)[0] if "." in m else ""
    for n in ast.walk(ast.parse(p.read_text())):
        if isinstance(n, ast.ImportFrom) and n.level:
            base = pkg.split(".") if pkg else []
            base = base[:len(base) - (n.level - 1)] if n.level > 1 else base
            tgt = ".".join(base + ([n.module] if n.module else []))
            cands = [tgt] if n.module else []
            cands += [f"{tgt}.{a.name}".strip(".") for a in n.names]
            for c in cands:
                if c in mods and c != m:
                    edges.add((m, c))
out = ['digraph G { graph [rankdir=LR, fontname="Helvetica", ranksep=1.1, nodesep=0.12, concentrate=true];',
       ' node [fontname="Helvetica", fontsize=9, shape=box, style="rounded,filled", height=0.25];',
       ' edge [color="#88888888", arrowsize=0.5];']
for m in sorted(mods):
    out.append(f' "{m}" [fillcolor="{COL[GROUP.get(m, "core")]}"];')
for a, b in sorted(edges):
    out.append(f' "{a}" -> "{b}";')
out.append("}")
Path(__file__).with_name("modules.dot").write_text("\n".join(out) + "\n")
print(len(mods), "modules,", len(edges), "edges")
