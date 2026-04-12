"""Marko extensions for KEBAB markdown — footnotes with structured fields.

Based on marko's built-in ``footnote`` extension but with typed, structured
fields (``number``, ``title``, ``url``, ``source_id``) instead of generic
string labels and parsed children. This makes footnote operations (dedup,
count, create) trivial tree traversals instead of regex.

Two node types:

- :class:`FootnoteDef` — block-level ``[^N]: [Title](URL)`` with optional
  ``[source_id]`` prefix.
- :class:`FootnoteRef` — inline ``[^N]`` reference.

Usage::

    from marko import Markdown
    md = Markdown(extensions=["gfm", markdown_ext.make_extension()])
    tree = md.parse(body)
    body_out = md.render(tree)  # roundtrip-safe
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterator, cast

from marko import block, helpers, inline
from marko.md_renderer import MarkdownRenderer

if TYPE_CHECKING:
    from marko.inline_parser import _Match
    from marko.source import Source


# ---------------------------------------------------------------------------
# Block element: [^N]: [optional_id] [Title](URL)
# ---------------------------------------------------------------------------

# Pattern: [^<digits>]: optional-[id]-prefix [Title](URL)
# Groups: 1=number, 2=rest-of-line (parsed further in __init__)
# No ^ anchor — marko's source.expect_re calls re.match(buffer, pos)
# which already pins to the current position; ^ would fail at pos > 0.
_DEF_RE = re.compile(r" {0,3}\[\^(\d+)\]:\s*([^\n]+)")

# Extracts optional [source_id] prefix, then [title](url)
_CONTENT_RE = re.compile(
    r"(?:\[(\d+)\]\s*)?"       # optional [source_id] prefix
    r"\[([^\]]*)\]"            # [title]
    r"\(([^)]+)\)"             # (url)
)


class FootnoteDef(block.BlockElement):
    """Block-level footnote definition with structured fields.

    Attributes:
        number:    The footnote number (from ``[^N]``).
        title:     Display text inside the link.
        url:       URL or relative path.
        source_id: Optional integer prefix for local sources (``[42]``).
    """

    priority = 6
    number: int
    title: str
    url: str
    source_id: int | None

    def __init__(self, match: re.Match[str]) -> None:
        self.number = int(match.group(1))
        rest = match.group(2).strip()
        content_match = _CONTENT_RE.match(rest)
        if content_match:
            sid = content_match.group(1)
            self.source_id = int(sid) if sid else None
            self.title = content_match.group(2)
            self.url = content_match.group(3)
        else:
            # Fallback: raw content without link structure
            self.source_id = None
            self.title = rest
            self.url = ""
        self.children = []  # type: ignore[assignment]

    @classmethod
    def create(
        cls,
        number: int,
        title: str,
        url: str,
        source_id: int | None = None,
    ) -> FootnoteDef:
        """Construct a FootnoteDef programmatically (no regex match needed)."""
        instance = cls.__new__(cls)
        instance.number = number
        instance.title = title
        instance.url = url
        instance.source_id = source_id
        instance.children = []  # type: ignore[assignment]
        return instance

    @classmethod
    def match(cls, source: Source) -> Any:
        return source.expect_re(_DEF_RE)

    @classmethod
    def parse(cls, source: Source) -> FootnoteDef:
        state = cls(cast(re.Match[str], source.match))
        source.consume()
        return state


# ---------------------------------------------------------------------------
# Inline element: [^N]
# ---------------------------------------------------------------------------

_REF_RE = re.compile(r"\[\^(\d+)\]")


class FootnoteRef(inline.InlineElement):
    """Inline footnote reference ``[^N]``."""

    pattern = _REF_RE
    priority = 6
    parse_children = False
    number: int

    def __init__(self, match: _Match) -> None:
        self.number = int(match.group(1))
        self.children = match.group(0)  # type: ignore[assignment]

    @classmethod
    def find(cls, text: str, *, source: Source) -> Iterator[_Match]:
        if isinstance(cls.pattern, str):
            cls.pattern = re.compile(cls.pattern)
        yield from cls.pattern.finditer(text)


# ---------------------------------------------------------------------------
# Renderer mixin — markdown-to-markdown roundtrip
# ---------------------------------------------------------------------------


class FootnoteRendererMixin:
    """Adds ``render_footnote_def`` and ``render_footnote_ref`` to the renderer."""

    @helpers.render_dispatch(MarkdownRenderer)
    def render_footnote_def(self, element: Any) -> str:
        prefix = f"[{element.source_id}] " if element.source_id is not None else ""
        if element.url:
            return f"[^{element.number}]: {prefix}[{element.title}]({element.url})\n"
        # Raw content fallback (no link structure)
        return f"[^{element.number}]: {element.title}\n"

    @helpers.render_dispatch(MarkdownRenderer)
    def render_footnote_ref(self, element: Any) -> str:
        return f"[^{element.number}]"


# ---------------------------------------------------------------------------
# Extension factory
# ---------------------------------------------------------------------------


def make_extension() -> helpers.MarkoExtension:
    """Return the KEBAB footnote extension for ``marko.Markdown(extensions=[...])``."""
    return helpers.MarkoExtension(
        elements=[FootnoteDef, FootnoteRef],
        renderer_mixins=[FootnoteRendererMixin],
    )


__all__ = ["FootnoteDef", "FootnoteRef", "make_extension"]
