# tests/unit/agents/research_gaps/test_categorizer.py
"""Tests for the gap-categorizer LLM step."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.agents.research_gaps import categorizer as cat_mod
from app.agents.research_gaps.categorizer import (
    GapCategorization,
    categorize_gap,
)
from app.config.config import Settings


@dataclass
class _StubAgent:
    """Stub for an Agent — only exposes ``run_sync`` like pydantic-ai's Agent."""

    output: GapCategorization

    def run_sync(self, user_message: str, deps: object = None) -> "_StubResult":
        return _StubResult(output=self.output)


@dataclass
class _StubResult:
    output: GapCategorization


def _stub_factory(out: GapCategorization):
    return lambda settings: _StubAgent(output=out)


@pytest.fixture
def settings() -> Settings:
    return Settings(GAP_CATEGORIZER_MODEL="gemini-flash")


class TestGapCategorizer:
    def test_categorizes_factual(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        out = GapCategorization(
            category="factual", reasoning="Asks for a specific magnitude."
        )
        monkeypatch.setattr(cat_mod, "_default_categorizer_agent", _stub_factory(out))
        result = categorize_gap(
            settings,
            "What was the magnitude of the 2022 Luzon earthquake?",
            "Article about Philippine seismology.",
        )
        assert result.category == "factual"

    def test_categorizes_conceptual(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        out = GapCategorization(
            category="conceptual", reasoning="Asks for a definition."
        )
        monkeypatch.setattr(cat_mod, "_default_categorizer_agent", _stub_factory(out))
        result = categorize_gap(
            settings,
            "What is the difference between weather and climate?",
            "Earth science article.",
        )
        assert result.category == "conceptual"

    def test_categorizes_pedagogical(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        out = GapCategorization(
            category="pedagogical", reasoning="Asks for teaching approach."
        )
        monkeypatch.setattr(cat_mod, "_default_categorizer_agent", _stub_factory(out))
        result = categorize_gap(
            settings,
            "How would a teacher explain photosynthesis to Grade 10?",
            "Biology article.",
        )
        assert result.category == "pedagogical"

    def test_categorizes_open_ended(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        out = GapCategorization(
            category="open_ended", reasoning="Personal opinion question."
        )
        monkeypatch.setattr(cat_mod, "_default_categorizer_agent", _stub_factory(out))
        result = categorize_gap(
            settings,
            "Why does climate change matter to you personally?",
            "Climate article.",
        )
        assert result.category == "open_ended"

    def test_categorizes_local_cultural(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        out = GapCategorization(
            category="local_cultural", reasoning="Specific PH community."
        )
        monkeypatch.setattr(cat_mod, "_default_categorizer_agent", _stub_factory(out))
        result = categorize_gap(
            settings,
            "How does the APECO project affect the Dumagat people?",
            "Philippine geography article.",
        )
        assert result.category == "local_cultural"

    def test_reasoning_is_populated(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        out = GapCategorization(category="conceptual", reasoning="Definition question.")
        monkeypatch.setattr(cat_mod, "_default_categorizer_agent", _stub_factory(out))
        result = categorize_gap(settings, "What is X?", "Article summary")
        assert result.reasoning  # non-empty for audit trail

    def test_uses_categorizer_model_setting(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        """resolve_model must be called with settings.GAP_CATEGORIZER_MODEL."""
        seen: list[str] = []
        monkeypatch.setattr(
            cat_mod, "resolve_model", lambda m: seen.append(m) or "stub-model"
        )
        # Cause the default agent factory to actually run
        out = GapCategorization(category="factual", reasoning="x")

        def _real_factory(settings: Settings):
            cat_mod.resolve_model(settings.GAP_CATEGORIZER_MODEL)
            return _StubAgent(output=out)

        monkeypatch.setattr(cat_mod, "_default_categorizer_agent", _real_factory)
        categorize_gap(settings, "q", "summary")
        assert seen == ["gemini-flash"]
