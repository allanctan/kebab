# tests/unit/agents/research_gaps/test_ai_verifier.py
"""Tests for the Gemini Pro cross-family verifier."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.agents.research_gaps import ai_verifier as ver_mod
from app.agents.research_gaps.ai_verifier import (
    VerifierVerdict,
    verify_ai_answer,
    verify_passes,
)
from app.config.config import Settings


@dataclass
class _StubAgent:
    output: VerifierVerdict

    def run_sync(self, user_message: str, deps: object = None) -> "_StubResult":
        return _StubResult(output=self.output)


@dataclass
class _StubResult:
    output: VerifierVerdict


def _stub_factory(out: VerifierVerdict):
    return lambda settings: _StubAgent(output=out)


@pytest.fixture
def settings() -> Settings:
    return Settings(AI_VERIFIER_MODEL="gemini-pro")


class TestVerifyGate:
    def test_agree_high_passes(self) -> None:
        v = VerifierVerdict(verdict="agree", confidence="high", issue="")
        assert verify_passes(v)

    def test_agree_medium_passes(self) -> None:
        v = VerifierVerdict(verdict="agree", confidence="medium", issue="")
        assert verify_passes(v)

    def test_agree_low_blocks(self) -> None:
        v = VerifierVerdict(verdict="agree", confidence="low", issue="")
        assert not verify_passes(v)

    def test_disagree_high_blocks(self) -> None:
        v = VerifierVerdict(verdict="disagree", confidence="high", issue="wrong")
        assert not verify_passes(v)

    def test_disagree_medium_blocks(self) -> None:
        v = VerifierVerdict(verdict="disagree", confidence="medium", issue="wrong")
        assert not verify_passes(v)

    def test_disagree_low_blocks(self) -> None:
        v = VerifierVerdict(verdict="disagree", confidence="low", issue="wrong")
        assert not verify_passes(v)

    def test_partial_high_passes(self) -> None:
        v = VerifierVerdict(verdict="partial", confidence="high", issue="x")
        assert verify_passes(v)

    def test_partial_medium_blocks(self) -> None:
        v = VerifierVerdict(verdict="partial", confidence="medium", issue="x")
        assert not verify_passes(v)

    def test_partial_low_blocks(self) -> None:
        v = VerifierVerdict(verdict="partial", confidence="low", issue="x")
        assert not verify_passes(v)


class TestVerifyAIAnswer:
    def test_agree_returned(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        stub = VerifierVerdict(verdict="agree", confidence="high", issue="")
        monkeypatch.setattr(ver_mod, "_default_verifier_agent", _stub_factory(stub))
        v = verify_ai_answer(
            settings,
            question="What is X?",
            answer="X is Y.",
            article_summary="An article.",
        )
        assert v.verdict == "agree"
        assert v.confidence == "high"

    def test_disagree_with_issue_returned(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        stub = VerifierVerdict(
            verdict="disagree",
            confidence="high",
            issue="Conflates A with B; these are distinct.",
        )
        monkeypatch.setattr(ver_mod, "_default_verifier_agent", _stub_factory(stub))
        v = verify_ai_answer(
            settings,
            question="What is X?",
            answer="X equals A.",
            article_summary="An article.",
        )
        assert v.verdict == "disagree"
        assert "Conflates" in v.issue

    def test_verifier_does_not_see_article_body(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        captured: dict[str, str] = {}

        @dataclass
        class _Cap:
            output: VerifierVerdict

            def run_sync(self, user: str, deps: object = None) -> _StubResult:
                captured["user"] = user
                return _StubResult(output=self.output)

        stub = VerifierVerdict(verdict="agree", confidence="high", issue="")
        monkeypatch.setattr(
            ver_mod,
            "_default_verifier_agent",
            lambda settings: _Cap(output=stub),
        )
        verify_ai_answer(
            settings,
            question="Q",
            answer="A",
            article_summary="SUM",
        )
        # Summary is included, but body must NOT be passed in (independence)
        assert "SUM" in captured["user"]
        assert "article_body" not in captured["user"].lower()
