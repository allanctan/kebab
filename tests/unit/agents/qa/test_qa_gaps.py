"""Tests for QA gap discovery."""

from __future__ import annotations

from app.agents.qa.qa import GapQuestion, QaResult, QaPair
from app.models.source import Source


class TestGapQuestion:
    def test_gap_question_model(self) -> None:
        gq = GapQuestion(
            question="How does slab pull compare to convection?",
            reasoning="Article mentions convection but not slab pull.",
        )
        assert gq.question == "How does slab pull compare to convection?"
        assert gq.reasoning != ""


class TestQaResultWithGaps:
    def test_result_with_gaps(self) -> None:
        result = QaResult(
            reasoning="Found gaps in coverage.",
            new_questions=[
                QaPair(
                    question="What are the three types?",
                    answer="Divergent, convergent, transform.",
                    sources=[Source(title="Source", tier=2)],
                ),
            ],
            gap_questions=[
                GapQuestion(
                    question="How does slab pull work?",
                    reasoning="Not covered in article.",
                ),
            ],
            is_ready_to_commit=True,
        )
        assert len(result.new_questions) == 1
        assert len(result.gap_questions) == 1

    def test_result_without_gaps(self) -> None:
        result = QaResult(
            reasoning="No gaps found.",
            new_questions=[],
            gap_questions=[],
            is_ready_to_commit=False,
        )
        assert len(result.gap_questions) == 0

    def test_result_gaps_only(self) -> None:
        result = QaResult(
            reasoning="Only gaps, no grounded pairs.",
            new_questions=[],
            gap_questions=[
                GapQuestion(question="Deeper question?", reasoning="Beyond scope."),
            ],
            is_ready_to_commit=True,
        )
        assert len(result.gap_questions) == 1
        assert result.is_ready_to_commit is True
