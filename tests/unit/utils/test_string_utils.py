"""String case conversions."""

from __future__ import annotations

import pytest

from app.utils.string_utils import to_camel_case, to_human_readable, to_pascal_case


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("snake_case_value", "Snake Case Value"),
        ("kebab-case-value", "Kebab Case Value"),
        ("camelCaseValue", "Camel Case Value"),
        ("PascalCaseValue", "Pascal Case Value"),
    ],
)
def test_to_human_readable(value: str, expected: str) -> None:
    assert to_human_readable(value) == expected


def test_to_pascal_case() -> None:
    assert to_pascal_case("snake_case_value") == "SnakeCaseValue"


def test_to_camel_case() -> None:
    assert to_camel_case("snake_case_value") == "snakeCaseValue"
    assert to_camel_case("") == ""
