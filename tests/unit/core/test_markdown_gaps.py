"""Tests for research gap markdown helpers."""

from __future__ import annotations

from app.core.markdown import (
    append_research_gaps,
    extract_research_gaps,
    parse_body,
    remove_research_gap,
)


class TestExtractResearchGaps:
    def test_extracts_gap_questions(self) -> None:
        body = (
            "# Article\n\nContent.\n\n"
            "## Research Gaps\n\n"
            "- How does slab pull compare to convection?\n"
            "- What role does asthenosphere viscosity play?\n"
        )
        gaps = extract_research_gaps(parse_body(body))
        assert len(gaps) == 2
        assert "slab pull" in gaps[0]
        assert "viscosity" in gaps[1]

    def test_empty_when_no_section(self) -> None:
        assert extract_research_gaps(parse_body("# Article\n\nContent.")) == []

    def test_empty_when_section_empty(self) -> None:
        assert extract_research_gaps(parse_body("# Article\n\n## Research Gaps\n\n")) == []

    def test_ignores_non_list_lines(self) -> None:
        body = (
            "## Research Gaps\n\n"
            "Some intro text.\n"
            "- Actual question?\n"
            "More text.\n"
        )
        gaps = extract_research_gaps(parse_body(body))
        assert len(gaps) == 1


class TestRemoveResearchGap:
    def test_removes_specific_gap(self) -> None:
        body = (
            "# Article\n\n"
            "## Research Gaps\n\n"
            "- Question one?\n"
            "- Question two?\n"
            "- Question three?\n"
        )
        result = remove_research_gap(body, "Question two?")
        gaps = extract_research_gaps(parse_body(result))
        assert len(gaps) == 2
        assert "Question two?" not in result

    def test_removes_section_when_last_gap(self) -> None:
        body = "# Article\n\n## Research Gaps\n\n- Only question?\n"
        result = remove_research_gap(body, "Only question?")
        assert "## Research Gaps" not in result

    def test_noop_when_gap_not_found(self) -> None:
        body = "# Article\n\n## Research Gaps\n\n- Real question?\n"
        result = remove_research_gap(body, "Nonexistent question")
        assert result == body


class TestAppendResearchGaps:
    def test_creates_section(self) -> None:
        body = "# Article\n\nContent.\n"
        result = append_research_gaps(body, ["Gap one?", "Gap two?"])
        assert "## Research Gaps" in result
        assert "- Gap one?" in result
        assert "- Gap two?" in result

    def test_appends_to_existing_section(self) -> None:
        body = "# Article\n\n## Research Gaps\n\n- Existing gap?\n"
        result = append_research_gaps(body, ["New gap?"])
        assert "- Existing gap?" in result
        assert "- New gap?" in result

    def test_skips_duplicates(self) -> None:
        body = "# Article\n\n## Research Gaps\n\n- Existing gap?\n"
        result = append_research_gaps(body, ["Existing gap?", "New gap?"])
        assert result.count("Existing gap?") == 1
        assert "- New gap?" in result

    def test_empty_list_returns_unchanged(self) -> None:
        body = "# Article\n\nContent.\n"
        assert append_research_gaps(body, []) == body
