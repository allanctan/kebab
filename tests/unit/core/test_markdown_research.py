"""Tests for research-related markdown helpers."""

from __future__ import annotations

from app.core.markdown import count_external_footnotes, extract_disputes, next_footnote_number, parse_body


class TestCountExternalFootnotes:
    def test_counts_http_footnotes(self) -> None:
        body = (
            "Claim one[^1]. Claim two[^2][^3].\n\n"
            "[^1]: [1] [Local Source](../raw/doc.pdf)\n"
            "[^2]: [Wikipedia: Plate tectonics](https://en.wikipedia.org/wiki/Plate_tectonics)\n"
            "[^3]: [OpenStax: Geology](https://openstax.org/books/geology/pages/1)\n"
        )
        assert count_external_footnotes(parse_body(body)) == 2

    def test_zero_when_no_external(self) -> None:
        body = "Claim[^1].\n\n[^1]: [1] [Local](../raw/doc.pdf)\n"
        assert count_external_footnotes(parse_body(body)) == 0

    def test_zero_when_no_footnotes(self) -> None:
        assert count_external_footnotes(parse_body("Just a body.")) == 0


class TestExtractDisputes:
    def test_extracts_dispute_entries(self) -> None:
        body = (
            "# Article\n\nContent.\n\n"
            "## Disputes\n\n"
            "- **Claim**: \"Convection is the sole driver\"\n"
            "  **Section**: Causes, paragraph 2\n"
            "  **External source**: [Wikipedia](https://...)\n"
            "  **Contradiction**: Slab pull is dominant.\n\n"
            "- **Claim**: \"All plates move at the same speed\"\n"
            "  **Section**: Movement, paragraph 1\n"
            "  **External source**: [OpenStax](https://...)\n"
            "  **Contradiction**: Speeds vary.\n"
        )
        resolvable, unresolvable = extract_disputes(parse_body(body))
        assert resolvable == 2
        assert unresolvable == 0

    def test_zero_when_no_disputes_section(self) -> None:
        resolvable, unresolvable = extract_disputes(parse_body("# Article\n\nContent."))
        assert resolvable == 0
        assert unresolvable == 0

    def test_zero_when_empty_disputes_section(self) -> None:
        resolvable, unresolvable = extract_disputes(parse_body("# Article\n\n## Disputes\n\n"))
        assert resolvable == 0
        assert unresolvable == 0

    def test_counts_resolvable_disputes_only(self) -> None:
        body = (
            "## Disputes\n\n"
            "- **Claim**: \"Plates move slowly.\"\n"
            "  **Section**: Intro\n\n"
            "- **Claim**: \"Convection is the main driver.\"\n"
            "  **Section**: Forces\n"
        )
        resolvable, unresolvable = extract_disputes(parse_body(body))
        assert resolvable == 2
        assert unresolvable == 0

    def test_separates_unresolvable_disputes(self) -> None:
        body = (
            "## Disputes\n\n"
            "- **Claim**: \"Plates move slowly.\"\n"
            "  **Section**: Intro\n\n"
            "<!-- unresolvable -->\n"
            "- **Claim**: \"Convection is the main driver.\"\n"
            "  **Section**: Forces\n"
        )
        resolvable, unresolvable = extract_disputes(parse_body(body))
        assert resolvable == 1
        assert unresolvable == 1

    def test_returns_zero_zero_when_no_disputes(self) -> None:
        resolvable, unresolvable = extract_disputes(parse_body("## Content\n\nBody.\n"))
        assert resolvable == 0
        assert unresolvable == 0


class TestNextFootnoteNumber:
    def test_returns_next_after_highest(self) -> None:
        body = "Text[^1] more[^3].\n\n[^1]: [Source](src)\n[^3]: [Source](src)\n"
        assert next_footnote_number(parse_body(body)) == 4

    def test_returns_1_when_no_footnotes(self) -> None:
        assert next_footnote_number(parse_body("No footnotes.")) == 1
