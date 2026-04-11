"""String case conversions.

Pattern adapted from ``better-ed-ai/app/utils/string_utils.py``.
"""

from __future__ import annotations

import re

_SNAKE_OR_KEBAB = re.compile(r"[_\-]+")
_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def to_human_readable(value: str) -> str:
    """Convert ``snake_case``/``kebab-case``/``camelCase`` → ``Title Case``."""
    spaced = _SNAKE_OR_KEBAB.sub(" ", value)
    spaced = _CAMEL_BOUNDARY.sub(" ", spaced)
    return " ".join(word.capitalize() for word in spaced.split())


def to_pascal_case(value: str) -> str:
    """Convert any common case to ``PascalCase``."""
    parts = _SNAKE_OR_KEBAB.sub(" ", _CAMEL_BOUNDARY.sub(" ", value)).split()
    return "".join(part.capitalize() for part in parts)


def to_camel_case(value: str) -> str:
    """Convert any common case to ``camelCase``."""
    pascal = to_pascal_case(value)
    return pascal[:1].lower() + pascal[1:] if pascal else pascal
