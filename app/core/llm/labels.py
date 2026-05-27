"""Convert resolved model IDs to short human-readable labels.

Used by the writer to render "answered by X, verified by Y" disclaimers
in AI-synthesized gap answers. Pure string mapping — no I/O, no settings
lookup.
"""

from __future__ import annotations

import re

_CLAUDE_RE = re.compile(r"(?:.*\.)?(claude-[a-z]+-\d+)-(\d+)")
_GEMINI_RE = re.compile(r"(gemini-\d+(?:\.\d+)?-[a-z]+)(?:-preview)?")


def short_model_label(resolved_id: str) -> str:
    """Return a short human-readable label for a resolved model id.

    Examples:
        ``anthropic:claude-opus-4-7``                → ``claude-opus-4.7``
        ``bedrock:us.anthropic.claude-sonnet-4-6``   → ``claude-sonnet-4.6``
        ``google-gla:gemini-3.1-pro-preview``        → ``gemini-3.1-pro``
        ``openai:gpt-5.4-mini``                      → ``gpt-5.4-mini``

    If the id has no provider prefix (caller forgot to resolve an alias),
    returns it unchanged.
    """
    model = resolved_id.split(":", 1)[1] if ":" in resolved_id else resolved_id

    m = _CLAUDE_RE.search(model)
    if m:
        # "claude-opus-4" + "7" → "claude-opus-4.7"
        return f"{m.group(1)}.{m.group(2)}"

    m = _GEMINI_RE.search(model)
    if m:
        return m.group(1)

    return model


__all__ = ["short_model_label"]
