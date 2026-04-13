"""Tests for QA gap discovery models."""

from __future__ import annotations

from app.agents.qa.qa import GapQuestion, GapDiscoveryResult


class TestGapDiscoveryResult:
    def test_with_gaps(self) -> None:
        result = GapDiscoveryResult(
            gap_questions=[
                GapQuestion(
                    question="What is the Ring of Fire?",
                    reasoning="Article mentions plate boundaries but not this concept.",
                ),
            ],
        )
        assert len(result.gap_questions) == 1

    def test_empty_is_valid(self) -> None:
        result = GapDiscoveryResult(gap_questions=[])
        assert result.gap_questions == []
