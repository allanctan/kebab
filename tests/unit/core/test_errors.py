"""Errors preserve cause chain."""

from __future__ import annotations

import pytest

from app.core.errors import KebabError, MarkdownError


def test_markdown_error_subclasses_kebab_error() -> None:
    assert issubclass(MarkdownError, KebabError)


def test_error_chain_preserved() -> None:
    original = ValueError("bad yaml")
    with pytest.raises(MarkdownError) as info:
        try:
            raise original
        except ValueError as exc:
            raise MarkdownError("wrapped") from exc
    assert info.value.__cause__ is original
