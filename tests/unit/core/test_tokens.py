"""Token counter sanity + limit enforcement."""

from __future__ import annotations

import pytest

from app.core.errors import KebabError
from app.core.llm.tokens import count_tokens, enforce_token_limit


def test_count_tokens_nonempty() -> None:
    assert count_tokens("hello world") > 0


def test_count_tokens_empty_is_zero() -> None:
    assert count_tokens("") == 0


def test_enforce_token_limit_passes_under_limit() -> None:
    enforce_token_limit("hi", limit=100)


def test_enforce_token_limit_raises_over_limit() -> None:
    with pytest.raises(KebabError):
        enforce_token_limit("a b c d e f g h", limit=2)
