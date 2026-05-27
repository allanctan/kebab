# tests/unit/agents/research_gaps/test_batch_categorizer.py
"""Tests for the batch gap-categorizer LLM step."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from app.agents.research_gaps import categorizer as cat_mod
from app.agents.research_gaps.categorizer import (
    BatchGapCategorization,
    GapCategorization,
    batch_categorize_gaps,
)
from app.config.config import Settings


@dataclass
class _StubAgent:
    """Stub for an Agent — only exposes ``run_sync`` like pydantic-ai's Agent."""

    output: BatchGapCategorization

    def run_sync(self, user_message: str, deps: object = None) -> "_StubResult":
        return _StubResult(output=self.output)


@dataclass
class _StubResult:
    output: BatchGapCategorization


def _stub_factory(out: BatchGapCategorization):
    return lambda settings: _StubAgent(output=out)


@pytest.fixture
def settings() -> Settings:
    return Settings(GAP_CATEGORIZER_MODEL="gemini-flash")


class TestBatchGapCategorizer:
    def test_returns_one_categorization_per_question(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        questions = [
            "What is the magnitude of the 2022 Luzon earthquake?",
            "What is the difference between weather and climate?",
            "Why does this matter to you personally?",
        ]
        out = BatchGapCategorization(
            categorizations=[
                GapCategorization(category="factual", reasoning="number request"),
                GapCategorization(category="conceptual", reasoning="definition"),
                GapCategorization(category="open_ended", reasoning="opinion"),
            ]
        )
        monkeypatch.setattr(
            cat_mod, "_default_batch_categorizer_agent", _stub_factory(out)
        )
        result = batch_categorize_gaps(settings, questions, "An earth science article.")
        assert len(result) == len(questions)
        assert [r.category for r in result] == ["factual", "conceptual", "open_ended"]

    def test_pads_with_factual_default_when_agent_returns_fewer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        questions = ["q1", "q2", "q3"]
        # Agent only returns 1 categorization for 3 questions
        out = BatchGapCategorization(
            categorizations=[
                GapCategorization(category="conceptual", reasoning="only one"),
            ]
        )
        monkeypatch.setattr(
            cat_mod, "_default_batch_categorizer_agent", _stub_factory(out)
        )
        with caplog.at_level(logging.WARNING, logger=cat_mod.__name__):
            result = batch_categorize_gaps(settings, questions, "summary")
        assert len(result) == 3
        assert result[0].category == "conceptual"
        # Pads default to factual
        assert result[1].category == "factual"
        assert result[2].category == "factual"
        # Padding reason is on the padded entries
        assert "defaulting to factual" in result[1].reasoning
        # Warning is logged
        assert any(
            "returned 1 categorizations for 3 questions" in r.message
            for r in caplog.records
        )

    def test_truncates_when_agent_returns_more(
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        questions = ["q1", "q2"]
        out = BatchGapCategorization(
            categorizations=[
                GapCategorization(category="factual", reasoning="a"),
                GapCategorization(category="conceptual", reasoning="b"),
                GapCategorization(category="open_ended", reasoning="extra"),
                GapCategorization(category="pedagogical", reasoning="extra2"),
            ]
        )
        monkeypatch.setattr(
            cat_mod, "_default_batch_categorizer_agent", _stub_factory(out)
        )
        with caplog.at_level(logging.WARNING, logger=cat_mod.__name__):
            result = batch_categorize_gaps(settings, questions, "summary")
        assert len(result) == 2
        assert [r.category for r in result] == ["factual", "conceptual"]
        assert any("truncating to 2" in r.message for r in caplog.records)

    def test_empty_questions_returns_empty_list(self, settings: Settings) -> None:
        # Should not invoke the agent at all.
        result = batch_categorize_gaps(settings, [], "summary")
        assert result == []

    def test_uses_categorizer_model_setting(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        """``_default_batch_categorizer_agent`` resolves
        ``settings.GAP_CATEGORIZER_MODEL`` via ``resolve_model``."""
        seen: list[str] = []
        monkeypatch.setattr(
            cat_mod, "resolve_model", lambda m: seen.append(m) or "stub-model"
        )
        out = BatchGapCategorization(
            categorizations=[GapCategorization(category="factual", reasoning="x")]
        )

        def _real_factory(settings: Settings):
            cat_mod.resolve_model(settings.GAP_CATEGORIZER_MODEL)
            return _StubAgent(output=out)

        monkeypatch.setattr(cat_mod, "_default_batch_categorizer_agent", _real_factory)
        batch_categorize_gaps(settings, ["q"], "summary")
        assert seen == ["gemini-flash"]
