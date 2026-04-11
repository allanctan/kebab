"""Markdown read/write round-trip + FAQ extraction."""

from __future__ import annotations

from pathlib import Path

from app.core.markdown import extract_faq, extract_section, read_article, write_article
from app.models.frontmatter import FrontmatterSchema
from app.models.source import Source

ARTICLE = """---
id: SCI-BIO-001
name: Photosynthesis
type: article
sources:
  - id: 0
    title: OpenStax Biology 2e
    tier: 2
bloom_ceiling: 4
---

# Photosynthesis

Body text.

## Q&A

**Q: What is photosynthesis?**
A short answer.

**Q: Why is it important?**
Another answer.
"""


def test_round_trip_preserves_vertical_keys(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text(ARTICLE, encoding="utf-8")

    fm, body = read_article(path)
    assert fm.id == "SCI-BIO-001"
    assert fm.sources[0].tier == 2
    # Vertical-specific key passes through via extra="allow".
    assert fm.model_dump().get("bloom_ceiling") == 4

    out = tmp_path / "b.md"
    write_article(out, fm, body)
    fm2, body2 = read_article(out)
    assert fm2.id == fm.id
    assert fm2.model_dump().get("bloom_ceiling") == 4
    assert "Photosynthesis" in body2


def test_extract_faq_returns_questions() -> None:
    questions = extract_faq(ARTICLE)
    assert questions == ["What is photosynthesis?", "Why is it important?"]


def test_extract_faq_empty_when_missing_section() -> None:
    assert extract_faq("# Just a title\n") == []


def test_extract_section_returns_empty_string_when_missing() -> None:
    assert extract_section("no headings here", "Q&A") == ""


def test_write_article_minimal(tmp_path: Path) -> None:
    path = tmp_path / "min.md"
    fm = FrontmatterSchema(
        id="X-1",
        name="X",
        type="article",
        sources=[Source(id=0, title="t", tier=1)],
    )
    write_article(path, fm, "body\n")
    fm2, body2 = read_article(path)
    assert fm2.id == "X-1"
    assert "body" in body2
