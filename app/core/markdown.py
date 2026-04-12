"""Read and write curated markdown articles.

Primary parser: :mod:`frontmatter` for YAML frontmatter, :mod:`marko`
(with GFM + KEBAB footnote plugin) for the markdown body AST. A regex
fallback handles files with BOM/whitespace quirks in the YAML layer.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

import frontmatter
import marko
import yaml

from app.core.errors import MarkdownError
from app.core.markdown_ext import make_extension
from app.models.frontmatter import FrontmatterSchema

# Module-level marko parser: GFM + KEBAB footnotes, markdown renderer.
from marko.md_renderer import MarkdownRenderer

_md = marko.Markdown(renderer=MarkdownRenderer, extensions=["gfm", make_extension()])

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AST parsing / rendering
# ---------------------------------------------------------------------------


def parse_body(body: str) -> marko.block.Document:
    """Parse a markdown body string into an AST (GFM + footnotes)."""
    return _md.parse(body)


def render_body(tree: marko.block.Document) -> str:
    """Render an AST back to markdown string. Roundtrip-safe."""
    return _md.render(tree)


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
def _parse_path(path: Path) -> tuple[FrontmatterSchema, str, marko.block.Document]:
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
    tree = parse_body(body)
    return fm, body, tree


def read_article(path: Path) -> tuple[FrontmatterSchema, str, marko.block.Document]:
    """Parse a curated markdown file into ``(frontmatter, raw_body, AST)``.

    ``raw_body`` is the original string (for embedding, token counting).
    ``tree`` is the parsed marko AST (for structural reads and mutations).
    """
    return _parse_path(path)


def find_article_by_id(curated_dir: Path, article_id: str) -> Path | None:
    """Scan ``curated_dir`` recursively for the article with the given ID.

    Returns ``None`` if no curated markdown file has frontmatter ``id``
    matching ``article_id``. Files that fail to parse are skipped.
    """
    for path in curated_dir.rglob("*.md"):
        try:
            fm, _, _ = read_article(path)
        except Exception:
            continue
        if fm.id == article_id:
            return path
    return None


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


def extract_research_gaps(body: str) -> list[str]:
    """Extract gap questions from the ``## Research Gaps`` section."""
    section = extract_section(body, "Research Gaps")
    if not section:
        return []
    gaps: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            gaps.append(stripped[2:].strip())
    return gaps


def remove_research_gap(body: str, question: str) -> str:
    """Remove a specific gap question from ``## Research Gaps``.

    If the last gap is removed, the entire section is dropped.
    """
    line_to_remove = f"- {question}"
    lines = body.splitlines()
    new_lines = [line for line in lines if line.strip() != line_to_remove]
    if len(new_lines) == len(lines):
        return body
    result = "\n".join(new_lines)
    remaining_gaps = extract_research_gaps(result)
    if not remaining_gaps:
        result = re.sub(
            r"\n*^##\s+Research Gaps\s*\n*",
            "\n",
            result,
            flags=re.MULTILINE,
        ).rstrip() + "\n"
    return result


def append_research_gaps(body: str, gaps: list[str]) -> str:
    """Append gap questions to ``## Research Gaps``, creating section if needed.

    Skips questions already present in the section.
    """
    if not gaps:
        return body
    existing = set(extract_research_gaps(body))
    fresh = [g for g in gaps if g not in existing]
    if not fresh:
        return body
    new_lines = "\n".join(f"- {g}" for g in fresh)
    section = extract_section(body, "Research Gaps")
    if section:
        return body.rstrip() + "\n" + new_lines + "\n"
    return body.rstrip() + "\n\n## Research Gaps\n\n" + new_lines + "\n"
