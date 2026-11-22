"""litnav command line. Each command group lives in litnav/commands/ and
registers itself with `add(subparsers)`; `args.func(args)` runs it."""
from __future__ import annotations

import argparse
import importlib
import sys

from . import __version__

COMMANDS = ["core", "importing", "text", "searching", "refs", "reading_cmds", "derived_cmds",
            "discovery_cmds", "curation_cmds", "web_cmds", "model_cmds", "guard_cmds"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="litnav", description="A literature library for "
                                "researchers and their agents.")
    p.add_argument("--version", action="version", version=f"litnav {__version__}")
    p.add_argument("--library", "-L", help="library directory (default: $LITNAV_LIBRARY "
                   "or the nearest parent with library.toml)")
    p.add_argument("--model-profile", "-M", help="local model endpoint: a [local_model.profiles] "
                   "name, e.g. orchid (default: $LITNAV_MODEL_PROFILE, then [local_model] profile)")
    sub = p.add_subparsers(dest="command", metavar="<command>")
    for name in COMMANDS:
        try:
            mod = importlib.import_module(f"litnav.commands.{name}")
        except ModuleNotFoundError as e:
            if e.name == f"litnav.commands.{name}":
                continue
            raise
        mod.add(sub)
    return p


def main(argv=None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    if getattr(args, "model_profile", None):
        import os
        os.environ["LITNAV_MODEL_PROFILE"] = args.model_profile      # also for subprocesses
    if not getattr(args, "func", None):
        p.print_help()
        return 1
    from .config import LibraryNotFound
    from .library import KeyError_
    try:
        return args.func(args) or 0
    except (LibraryNotFound, KeyError_, FileExistsError, ValueError) as e:
        print(f"litnav: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
