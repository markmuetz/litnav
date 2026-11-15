"""import, import-bib, import-legacy, fetch."""
from __future__ import annotations

import sys
from pathlib import Path

from .. import importer
from ..library import Library


def add(sub):
    p = sub.add_parser("import", help="import PDFs (default: everything in _inbox/); dry run unless --apply")
    p.add_argument("files", nargs="*")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--from-plan", action="store_true", help="apply the last plan without re-querying")
    p.add_argument("--doi", help="use this DOI (single file)")
    p.add_argument("--key", help="use this key (single file)")
    p.add_argument("--tag", "-t", action="append", default=[])
    p.add_argument("--confidential", action="store_true",
                   help="never read or look up these files; needs --key or a canonical filename")
    p.add_argument("--move", action="store_true", help="move files from outside the inbox (default: copy)")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("import-bib", help="import a .bib with file fields (Zotero, JabRef, Mendeley)")
    p.add_argument("bibfile")
    p.add_argument("--pdf-root", help="base for relative file paths")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--tag", "-t", action="append", default=[])
    p.add_argument("--keywords-as-tags", action="store_true")
    p.set_defaults(func=cmd_import_bib)

    p = sub.add_parser("import-legacy", help="convert a litman data directory")
    p.add_argument("src")
    p.add_argument("--relations", help="TSV: key, relation, target")
    p.add_argument("--no-hash", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_import_legacy)

    p = sub.add_parser("fetch", help="open DOIs (or items' DOIs) in the browser to download")
    p.add_argument("ids", nargs="+")
    p.set_defaults(func=cmd_fetch)


def cmd_import(a):
    lib = Library.find(a.library)
    if a.from_plan:
        rows = importer.read_plan(lib)
        print(f"applying plan: {len(rows)} rows", file=sys.stderr)
        c = importer.apply(lib, rows, tags=a.tag, mode="move" if a.move else None)
        print(c, file=sys.stderr)
        return
    if a.files:
        tag = importer.CONFIDENTIAL_DIR if a.confidential else ""
        files = [(Path(f).expanduser().resolve(), tag) for f in a.files]
        if (a.doi or a.key) and len(files) > 1:
            raise ValueError("--doi/--key need a single file")
    else:
        files = importer.inbox_files(lib)
    print(f"{'APPLY' if a.apply else 'DRY RUN'}: {len(files)} PDFs", file=sys.stderr)
    rows = importer.plan(lib, files, doi=a.doi, key=a.key)
    importer.write_plan(lib, rows)
    from collections import Counter
    print("  " + "  ".join(f"{k}={v}" for k, v in Counter(r["status"] for r in rows).most_common()),
          file=sys.stderr)
    if a.apply:
        c = importer.apply(lib, rows, tags=a.tag, mode="move" if a.move else None)
        print(c, file=sys.stderr)
    else:
        print(f"plan written to {importer.plan_path(lib)}; --apply or --from-plan to import",
              file=sys.stderr)


def cmd_import_bib(a):
    from ..importers import bibtex
    lib = Library.find(a.library)
    rows = bibtex.plan(lib, Path(a.bibfile), Path(a.pdf_root) if a.pdf_root else None)
    if a.apply:
        bibtex.apply(lib, rows, tags=a.tag, keep_keywords_as_tags=a.keywords_as_tags)
    else:
        print("dry run; --apply to import", file=sys.stderr)


def cmd_import_legacy(a):
    from ..importers import legacy
    lib = Library.find(a.library)
    legacy.convert(lib, Path(a.src), relations=Path(a.relations) if a.relations else None,
                   hash_pdfs=not a.no_hash, apply=a.apply)
    if not a.apply:
        print("dry run; --apply to convert", file=sys.stderr)


def cmd_fetch(a):
    from ..net import open_in_browser
    lib = None
    for x in a.ids:
        doi = x
        if not x.startswith("10."):
            lib = lib or Library.find(a.library)
            doi = lib.get(x).doi
            if not doi:
                print(f"{x}: no DOI", file=sys.stderr)
                continue
        open_in_browser(f"https://doi.org/{doi}")
