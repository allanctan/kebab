"""Tests for app.agents.research_gaps.writer."""

from __future__ import annotations

from app.agents.research_gaps.writer import GapAnswer, apply_answers_to_gaps


class TestApplyAnswersToGaps:
    def test_replaces_question_with_qa_block(self) -> None:
        body = (
            "# Topic\n\n"
            "Body content.\n\n"
            "## Research Gaps\n\n"
            "- What is plate tectonics?\n"
            "- How fast do plates move?\n"
        )
        gaps = ["What is plate tectonics?", "How fast do plates move?"]
        answers = [
            GapAnswer(
                gap_idx=0,
                answer_text="A theory describing how Earth's lithosphere is divided.",
                source_title="Wikipedia: Plate tectonics",
                source_url="https://en.wikipedia.org/wiki/Plate_tectonics",
            )
        ]

        result = apply_answers_to_gaps(body, gaps, answers)

        assert "**Q: What is plate tectonics?**" in result
        assert "**A:** A theory describing" in result
        assert "(Source: [Wikipedia: Plate tectonics]" in result
        # Unanswered gap stays untouched
        assert "- How fast do plates move?" in result

    def test_unknown_gap_idx_is_skipped(self) -> None:
        body = "## Research Gaps\n\n- Q1\n"
        gaps = ["Q1"]
        answers = [
            GapAnswer(
                gap_idx=99,
                answer_text="X",
                source_title="T",
                source_url="https://example.com",
            )
        ]
        result = apply_answers_to_gaps(body, gaps, answers)
        assert result == body

    def test_strips_stray_footnote_refs(self) -> None:
        body = "## Research Gaps\n\n- Q1\n"
        gaps = ["Q1"]
        answers = [
            GapAnswer(
                gap_idx=0,
                answer_text="An answer with [^3] a leaked footnote.",
                source_title="T",
                source_url="https://example.com",
            )
        ]
        result = apply_answers_to_gaps(body, gaps, answers)
        assert "[^3]" not in result
        assert "An answer with" in result

    def test_empty_answers_returns_body_unchanged(self) -> None:
        body = "## Research Gaps\n\n- Q1\n"
        result = apply_answers_to_gaps(body, ["Q1"], [])
        assert result == body
