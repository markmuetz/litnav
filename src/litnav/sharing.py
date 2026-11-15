"""Sharing levels (design § 6) and the one table that enforces them.

Every code path that sends text out of the machine, or serves the library to
anyone but its owner, asks `allow(level, purpose)` -- normally through
`Library.items(purpose=...)` -- rather than checking tags itself.
"""
from __future__ import annotations

LEVELS = ("open", "licensed", "private", "confidential")

# Purposes, from least to most exposure.
LOCAL = "local"                  # owner, on this machine: everything
SEND_OUT = "send_out"            # text sent to an outside model or service for the owner
LOOKUP = "lookup"                # metadata (DOI, title) sent to Crossref/OpenAlex etc.
SERVE_META = "serve_meta"        # metadata, abstract, summary served to someone else
SERVE_TEXT = "serve_text"        # full text served to someone else

PURPOSES = (LOCAL, SEND_OUT, LOOKUP, SERVE_META, SERVE_TEXT)

_ALLOW = {
    "open":         {LOCAL, SEND_OUT, LOOKUP, SERVE_META, SERVE_TEXT},
    "licensed":     {LOCAL, SEND_OUT, LOOKUP, SERVE_META},
    "private":      {LOCAL, SEND_OUT, LOOKUP},
    "confidential": {LOCAL},
}


def check_level(level: str) -> str:
    if level not in LEVELS:
        raise ValueError(f"unknown sharing level {level!r}; use one of {', '.join(LEVELS)}")
    return level


def allow(level: str, purpose: str) -> bool:
    if purpose not in PURPOSES:
        raise ValueError(f"unknown purpose {purpose!r}")
    # An unknown level (a hand-edited typo) is treated as the most restrictive.
    return purpose in _ALLOW.get(level, _ALLOW["confidential"])
