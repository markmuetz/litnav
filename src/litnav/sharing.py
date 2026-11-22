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
# SEND_OUT in a library whose owner said no to sending licensed text to models
# (library.toml: licensed_to_models = false). Callers never pass this
# themselves: Library.purpose() maps SEND_OUT to it.
SEND_OUT_STRICT = "send_out_strict"
LOOKUP = "lookup"                # metadata (DOI, title) sent to Crossref/OpenAlex etc.
# Text sent to a model the owner runs: on this machine (Ollama, llama.cpp, MLX)
# or on the owner's own HPC account (a batch on JASMIN/ORCHID). Not a service.
LOCAL_MODEL = "local_model"
# Model-written derived text (summaries, verdicts), not the paper's own words,
# sent to an outside model. The owner's ruling: a summary may go where the
# paper's text may not.
SEND_DERIVED = "send_derived"
SERVE_META = "serve_meta"        # metadata, abstract, summary served to someone else
SERVE_TEXT = "serve_text"        # full text served to someone else

PURPOSES = (LOCAL, SEND_OUT, SEND_OUT_STRICT, LOOKUP, SERVE_META, SERVE_TEXT, LOCAL_MODEL,
            SEND_DERIVED)

_ALLOW = {
    "open":         {LOCAL, SEND_OUT, SEND_OUT_STRICT, LOOKUP, SERVE_META, SERVE_TEXT,
                     LOCAL_MODEL, SEND_DERIVED},
    "licensed":     {LOCAL, SEND_OUT, LOOKUP, SERVE_META, LOCAL_MODEL, SEND_DERIVED},
    "private":      {LOCAL, SEND_OUT, SEND_OUT_STRICT, LOOKUP, LOCAL_MODEL, SEND_DERIVED},
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


def effective(purpose: str, licensed_to_models: bool) -> str:
    """The purpose to check, given the library's answer to `init`'s question."""
    if purpose == SEND_OUT and not licensed_to_models:
        return SEND_OUT_STRICT
    return purpose


def agent_mode() -> bool:
    """True when litnav runs inside an agent's shell (Claude Code sets
    CLAUDECODE=1; other agents can set LITNAV_AGENT=1). Then CLI output is
    going to a model, so commands that print paper text check `send_out`.
    A guard against accidents, not against a determined agent."""
    import os
    return bool(os.environ.get("CLAUDECODE") or os.environ.get("LITNAV_AGENT"))
