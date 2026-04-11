"""Token counting via tiktoken.

Used to enforce ``Settings.MAX_TOKENS_PER_ARTICLE`` (default 50k).
"""

from __future__ import annotations

import logging
from functools import lru_cache

import tiktoken

from app.core.errors import KebabError

logger = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def _encoder(model: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """Return the token count of ``text`` for the given model encoding."""
    return len(_encoder(model).encode(text))


def enforce_token_limit(text: str, limit: int, model: str = "gpt-4o") -> int:
    """Raise :class:`KebabError` if ``text`` exceeds ``limit`` tokens."""
    n = count_tokens(text, model)
    if n > limit:
        raise KebabError(f"text exceeds token limit: {n} > {limit}")
    return n
