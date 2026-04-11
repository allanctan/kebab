"""Read and write curated markdown articles.

Primary parser: :mod:`frontmatter`. A regex fallback (adapted from
``better-ed-ai/app/core/parser.py::parse_yaml_frontmatter``) handles files
with BOM/whitespace quirks. Section extraction mirrors
``better-ed-ai/app/core/parser.py::_extract_md_section``.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

import frontmatter
import yaml

from app.core.errors import MarkdownError
from app.models.frontmatter import FrontmatterSchema

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(
    r"^\s*---\s*\n(?P<yaml>.*?)\n\s*---\s*\n?(?P<body>.*)$",
    re.DOTALL,
)


def _parse_yaml_frontmatter(text: str) -> tuple[dict, str]:
    """Regex fallback for files with BOM/whitespace the YAML loader trips on.

    Pattern adapted from
    ``better-ed-ai/app/core/parser.py::parse_yaml_frontmatter`` (lines 22–49)
    — strip BOM + leading whitespace upfront, then match. Unlike the source,
    we **raise** :class:`MarkdownError` on bad YAML rather than silently
    returning an empty dict.
    """
    cleaned = text.lstrip("\ufeff").lstrip()
    match = _FRONTMATTER_RE.match(cleaned)
    if not match:
        return {}, cleaned
    try:
        meta = yaml.safe_load(match.group("yaml")) or {}
    except yaml.YAMLError as exc:
        raise MarkdownError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise MarkdownError("frontmatter must be a YAML mapping")
    return meta, match.group("body").lstrip("\n")


@lru_cache(maxsize=512)
def _parse_path(path: Path) -> tuple[FrontmatterSchema, str]:
    raw = path.read_text(encoding="utf-8")
    try:
        post = frontmatter.loads(raw)
        meta, body = post.metadata, post.content
    except Exception as exc:  # noqa: BLE001 — fallback path
        logger.debug("frontmatter.loads failed for %s (%s); using regex fallback", path, exc)
        meta, body = _parse_yaml_frontmatter(raw)
    try:
        fm = FrontmatterSchema.model_validate(meta)
    except Exception as exc:
        raise MarkdownError(f"invalid frontmatter in {path}: {exc}") from exc
    return fm, body


def read_article(path: Path) -> tuple[FrontmatterSchema, str]:
    """Parse a curated markdown file into ``(frontmatter, body)``."""
    return _parse_path(path)


def write_article(path: Path, fm: FrontmatterSchema, body: str) -> None:
    """Serialize frontmatter + body back to disk preserving extra keys."""
    post = frontmatter.Post(content=body)
    post.metadata = fm.model_dump(mode="json", exclude_none=False)
    path.write_text(frontmatter.dumps(post, sort_keys=False), encoding="utf-8")
    _parse_path.cache_clear()


def extract_section(body: str, heading: str) -> str:
    """Return the text under ``## <heading>`` up to the next ``## `` or EOF.

    Case-insensitive heading match — adapted from
    ``better-ed-ai/app/core/parser.py::_extract_md_section`` (lines 52–56).
    """
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*\n(?P<section>.*?)(?=^##\s+|\Z)",
        re.DOTALL | re.MULTILINE | re.IGNORECASE,
    )
    match = pattern.search(body)
    return match.group("section").strip() if match else ""


def extract_faq(body: str) -> list[str]:
    """Extract questions from the ``## Q&A`` section.

    Spec §10: every ``**Q:`` line in the Q&A section becomes a FAQ entry.
    """
    section = extract_section(body, "Q&A")
    if not section:
        return []
    questions: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("**Q:"):
            text = stripped.removeprefix("**Q:").strip()
            text = text.removesuffix("**").strip()
            if text:
                questions.append(text)
    return questions


_FOOTNOTE_DEF_RE = re.compile(r"^\[\^(\d+)\]:\s*(.+)$", re.MULTILINE)
_EXTERNAL_URL_RE = re.compile(r"https?://")


def count_external_footnotes(body: str) -> int:
    """Count footnote definitions that link to external URLs (http/https)."""
    count = 0
    for match in _FOOTNOTE_DEF_RE.finditer(body):
        if _EXTERNAL_URL_RE.search(match.group(2)):
            count += 1
    return count


def extract_disputes(body: str) -> int:
    """Count dispute entries in the ``## Disputes`` section."""
    section = extract_section(body, "Disputes")
    if not section:
        return 0
    return section.count("- **Claim**:")


def next_footnote_number(body: str) -> int:
    """Return the next available footnote number (max existing + 1)."""
    numbers = [int(m.group(1)) for m in _FOOTNOTE_DEF_RE.finditer(body)]
    return max(numbers, default=0) + 1
