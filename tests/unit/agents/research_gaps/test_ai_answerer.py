# tests/unit/agents/research_gaps/test_ai_answerer.py
"""Tests for the Opus-driven AI answerer."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.agents.research_gaps import ai_answerer as ans_mod
from app.agents.research_gaps.ai_answerer import AIAnswer, ai_answer_gap
from app.config.config import Settings


@dataclass
class _StubAgent:
    output: AIAnswer

    def run_sync(self, user_message: str, deps: object = None) -> "_StubResult":
        return _StubResult(output=self.output)


@dataclass
class _StubResult:
    output: AIAnswer


def _stub_factory(out: AIAnswer):
    return lambda settings: _StubAgent(output=out)


@pytest.fixture
def settings() -> Settings:
    return Settings(AI_ANSWERER_MODEL="opus-4.7")


class TestAIAnswerer:
    def test_returns_answer_when_answerable(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        stub = AIAnswer(
            answer="Tectonic plates move at about 2 to 5 cm per year, "
            "driven by mantle convection currents.",
            grounded_in="general_knowledge",
            is_answered=True,
            reasoning="Well-established science.",
        )
        monkeypatch.setattr(ans_mod, "_default_answerer_agent", _stub_factory(stub))
        out = ai_answer_gap(
            settings,
            question="How fast do tectonic plates move?",
            article_body="A short article on plate tectonics.",
            article_summary="Plate tectonics for K-12.",
            category="conceptual",
        )
        assert out.is_answered
        assert "2 to 5 cm" in out.answer

    def test_refuses_genuinely_open_ended(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        stub = AIAnswer(
            answer="",
            grounded_in="general_knowledge",
            is_answered=False,
            reasoning="Personal opinion question.",
        )
        monkeypatch.setattr(ans_mod, "_default_answerer_agent", _stub_factory(stub))
        out = ai_answer_gap(
            settings,
            question="Why does this matter to you?",
            article_body="body",
            article_summary="summary",
            category="open_ended",
        )
        assert not out.is_answered

    def test_grounded_in_article_body_when_topic_present(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        stub = AIAnswer(
            answer="Photosynthesis converts light into glucose.",
            grounded_in="article_body",
            is_answered=True,
            reasoning="Article covers this directly.",
        )
        monkeypatch.setattr(ans_mod, "_default_answerer_agent", _stub_factory(stub))
        out = ai_answer_gap(
            settings,
            question="What does photosynthesis do?",
            article_body="Photosynthesis turns light into glucose.",
            article_summary="Bio article.",
            category="conceptual",
        )
        assert out.grounded_in == "article_body"

    def test_grounded_in_general_knowledge_when_outside(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        stub = AIAnswer(
            answer="Mitosis produces two identical daughter cells.",
            grounded_in="general_knowledge",
            is_answered=True,
            reasoning="Not in article body.",
        )
        monkeypatch.setattr(ans_mod, "_default_answerer_agent", _stub_factory(stub))
        out = ai_answer_gap(
            settings,
            question="What is mitosis?",
            article_body="An article about photosynthesis only.",
            article_summary="Bio article.",
            category="conceptual",
        )
        assert out.grounded_in == "general_knowledge"

    def test_passes_question_and_summary_to_agent(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        captured: dict[str, str] = {}

        @dataclass
        class _CapturingAgent:
            output: AIAnswer

            def run_sync(self, user: str, deps: object = None) -> _StubResult:
                captured["user"] = user
                return _StubResult(output=self.output)

        stub_out = AIAnswer(
            answer="x", grounded_in="general_knowledge", is_answered=True, reasoning="y"
        )
        monkeypatch.setattr(
            ans_mod,
            "_default_answerer_agent",
            lambda settings: _CapturingAgent(output=stub_out),
        )
        ai_answer_gap(
            settings,
            question="Q1",
            article_body="BODY",
            article_summary="SUMMARY",
            category="conceptual",
        )
        assert "Q1" in captured["user"]
        assert "SUMMARY" in captured["user"]
        # Category must be in the user message so the prompt can route tone
        assert "conceptual" in captured["user"]
