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

    def test_gap_idx_skips_already_answered_items(self) -> None:
        """When the body already has some answered Q/A blocks mixed with
        unanswered bullets, gap_idx must index into the unanswered-only
        list, not all list items. Otherwise gap_idx=1 would overwrite
        the first answered Q/A block instead of the second unanswered
        bullet.
        """
        body = (
            "## Research Gaps\n\n"
            "- First unanswered question?\n"
            "- **Q: Already answered?**\n"
            "  **A:** Previous answer. (Source: [Prev](https://example.com/prev))\n"
            "- Second unanswered question?\n"
            "- Third unanswered question?\n"
        )
        # gaps is filtered to unanswered only — 3 items
        gaps = [
            "First unanswered question?",
            "Second unanswered question?",
            "Third unanswered question?",
        ]
        # Answer gap_idx=1 ("Second unanswered question?")
        answers = [
            GapAnswer(
                gap_idx=1,
                answer_text="New answer to second.",
                source_title="NewSrc",
                source_url="https://example.com/new",
            )
        ]
        result = apply_answers_to_gaps(body, gaps, answers)

        # The previously-answered Q/A block must remain intact
        assert "**Q: Already answered?**" in result
        assert "Previous answer." in result
        # The second unanswered question should now be a Q/A block
        assert "**Q: Second unanswered question?**" in result
        assert "New answer to second." in result
        # First and third unanswered should remain as bullets
        assert "- First unanswered question?" in result
        assert "- Third unanswered question?" in result
