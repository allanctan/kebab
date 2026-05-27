# tests/unit/agents/research_gaps/test_orchestrator_routing.py
"""Tests for category-based routing inside research_gaps.run()."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from app.agents.research_gaps import research_gaps
from app.agents.research_gaps.ai_answerer import AIAnswer
from app.agents.research_gaps.ai_verifier import VerifierVerdict
from app.agents.research_gaps.categorizer import GapCategorization, GapCategory
from app.config.config import Settings


def _write_article(path: Path) -> None:
    path.write_text(
        "---\n"
        "id: TEST-001\n"
        "name: Test Article\n"
        "type: article\n"
        "section: Topic\n"
        "domain: science\n"
        "subdomain: general\n"
        "topic: x\n"
        "level_type: article\n"
        "confidence_level: 1\n"
        "parent_ids: []\n"
        "source_files: []\n"
        "summary: A test article about photosynthesis.\n"
        "---\n\n"
        "# Test Article\n\n"
        "Plants convert light into glucose.\n\n"
        "## Research Gaps\n\n"
        "- What is the difference between mitosis and meiosis?\n"
        "- Why does photosynthesis matter to you personally?\n"
        "- How does APECO affect local communities?\n",
        encoding="utf-8",
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    curated = tmp_path / "curated"
    curated.mkdir()
    s = Settings(
        CURATED_DIR=curated,
        AI_ANSWERER_MODEL="opus-4.7",
        AI_VERIFIER_MODEL="gemini-pro",
        GAP_CATEGORIZER_MODEL="gemini-flash",
        LOGS_DIR=str(tmp_path / "logs"),
    )
    return s


@pytest.fixture
def article(settings: Settings) -> Path:
    path = settings.CURATED_DIR / "test_article.md"
    _write_article(path)
    return path


def _stub_batch_categorize(category: str):
    """Return a stub that categorizes every input gap to ``category``."""

    def _stub(_settings, questions, _summary):
        return [
            GapCategorization(category=cast(GapCategory, category), reasoning="stub")
            for _ in questions
        ]

    return _stub


def _stub_answer(
    answered: bool, text: str = "Answer text.", grounding: str = "general_knowledge"
):
    return lambda _settings, **kw: AIAnswer(
        answer=text if answered else "",
        grounded_in=grounding,
        is_answered=answered,
        reasoning="stub",
    )


def _stub_verify(verdict: str, confidence: str, issue: str = ""):
    return lambda _settings, **kw: VerifierVerdict(
        verdict=verdict, confidence=confidence, issue=issue
    )


def _empty_plan():
    from app.agents.research_gaps.query_planner import GapQueryPlan

    return GapQueryPlan(queries=[])


class TestRouting:
    def test_conceptual_routes_through_answerer_then_verifier(
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
        article: Path,
    ) -> None:
        called: dict[str, int] = {"batch_categorize": 0, "answer": 0, "verify": 0}

        def batch_cat(_s, questions, _summary):
            called["batch_categorize"] += 1
            return [
                GapCategorization(category="conceptual", reasoning="def question")
                for _ in questions
            ]

        def ans(_s, **kw):
            called["answer"] += 1
            return AIAnswer(
                answer="Mitosis makes identical cells; meiosis makes gametes.",
                grounded_in="general_knowledge",
                is_answered=True,
                reasoning="ok",
            )

        def ver(_s, **kw):
            called["verify"] += 1
            return VerifierVerdict(verdict="agree", confidence="high", issue="")

        monkeypatch.setattr(research_gaps, "batch_categorize_gaps", batch_cat)
        monkeypatch.setattr(research_gaps, "ai_answer_gap", ans)
        monkeypatch.setattr(research_gaps, "verify_ai_answer", ver)
        monkeypatch.setattr(
            research_gaps, "plan_queries", lambda *a, **kw: _empty_plan()
        )
        monkeypatch.setattr(research_gaps, "search", lambda *a, **kw: [])

        result = research_gaps.run(settings, article_id="TEST-001", budget=20)
        assert called["batch_categorize"] == 1
        assert called["answer"] >= 1
        assert called["verify"] >= 1
        assert result.answered >= 1
        body = article.read_text(encoding="utf-8")
        assert "AI synthesis" in body
        assert "answered by" in body
        assert "verified by" in body

    def test_open_ended_writes_discussion_prompt_no_ai_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
        article: Path,
    ) -> None:
        called: dict[str, int] = {"answer": 0, "verify": 0}

        monkeypatch.setattr(
            research_gaps,
            "batch_categorize_gaps",
            _stub_batch_categorize("open_ended"),
        )

        def ans(_s, **kw):
            called["answer"] += 1
            return AIAnswer(
                answer="x",
                grounded_in="general_knowledge",
                is_answered=True,
                reasoning="ok",
            )

        def ver(_s, **kw):
            called["verify"] += 1
            return VerifierVerdict(verdict="agree", confidence="high", issue="")

        monkeypatch.setattr(research_gaps, "ai_answer_gap", ans)
        monkeypatch.setattr(research_gaps, "verify_ai_answer", ver)
        monkeypatch.setattr(
            research_gaps, "plan_queries", lambda *a, **kw: _empty_plan()
        )
        monkeypatch.setattr(research_gaps, "search", lambda *a, **kw: [])
        research_gaps.run(settings, article_id="TEST-001", budget=20)
        # open_ended should NOT call ai_answer_gap or verify_ai_answer
        assert called["answer"] == 0
        assert called["verify"] == 0
        body = article.read_text(encoding="utf-8")
        assert "*(open-ended discussion prompt" in body

    def test_verifier_disagree_suppresses_answer_and_writes_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
        article: Path,
    ) -> None:
        monkeypatch.setattr(
            research_gaps,
            "batch_categorize_gaps",
            _stub_batch_categorize("conceptual"),
        )
        # Opus says one thing
        monkeypatch.setattr(
            research_gaps,
            "ai_answer_gap",
            _stub_answer(answered=True, text="Opus's possibly-wrong claim."),
        )
        # Verifier disagrees
        monkeypatch.setattr(
            research_gaps,
            "verify_ai_answer",
            _stub_verify("disagree", "high", issue="conflates mitosis with meiosis"),
        )
        monkeypatch.setattr(
            research_gaps, "plan_queries", lambda *a, **kw: _empty_plan()
        )
        monkeypatch.setattr(research_gaps, "search", lambda *a, **kw: [])
        research_gaps.run(settings, article_id="TEST-001", budget=20)
        body = article.read_text(encoding="utf-8")
        # Rejected answer text is NOT in body
        assert "Opus's possibly-wrong claim" not in body
        # Italic rejection note IS in body
        assert "no defensible AI answer" in body
        assert "conflates mitosis with meiosis" in body[:5000]

    def test_verifier_low_confidence_suppresses(
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
        article: Path,
    ) -> None:
        monkeypatch.setattr(
            research_gaps,
            "batch_categorize_gaps",
            _stub_batch_categorize("conceptual"),
        )
        monkeypatch.setattr(
            research_gaps,
            "ai_answer_gap",
            _stub_answer(answered=True, text="An answer."),
        )
        monkeypatch.setattr(
            research_gaps, "verify_ai_answer", _stub_verify("agree", "low")
        )
        monkeypatch.setattr(
            research_gaps, "plan_queries", lambda *a, **kw: _empty_plan()
        )
        monkeypatch.setattr(research_gaps, "search", lambda *a, **kw: [])
        research_gaps.run(settings, article_id="TEST-001", budget=20)
        body = article.read_text(encoding="utf-8")
        assert "verifier confidence low" in body

    def test_no_ai_flag_skips_ai_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
        article: Path,
    ) -> None:
        called: dict[str, int] = {"batch_categorize": 0, "answer": 0}

        def batch_cat(_s, questions, _summary):
            called["batch_categorize"] += 1
            return [
                GapCategorization(category="conceptual", reasoning="x")
                for _ in questions
            ]

        def ans(_s, **kw):
            called["answer"] += 1
            return AIAnswer(
                answer="x",
                grounded_in="general_knowledge",
                is_answered=True,
                reasoning="x",
            )

        monkeypatch.setattr(research_gaps, "batch_categorize_gaps", batch_cat)
        monkeypatch.setattr(research_gaps, "ai_answer_gap", ans)
        monkeypatch.setattr(
            research_gaps, "plan_queries", lambda *a, **kw: _empty_plan()
        )
        monkeypatch.setattr(research_gaps, "search", lambda *a, **kw: [])
        research_gaps.run(settings, article_id="TEST-001", budget=20, no_ai=True)
        # batch_categorize MUST be skipped under no_ai, and ai_answer_gap NEVER called
        assert called["batch_categorize"] == 0
        assert called["answer"] == 0

    def test_same_family_warning_emitted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
        article: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Both fields resolve to anthropic family
        same = Settings(
            CURATED_DIR=settings.CURATED_DIR,
            AI_ANSWERER_MODEL="anthropic:claude-opus-4-7",
            AI_VERIFIER_MODEL="anthropic:claude-sonnet-4-5",
            GAP_CATEGORIZER_MODEL="gemini-flash",
            LOGS_DIR=str(settings.CURATED_DIR.parent / "logs2"),
        )
        monkeypatch.setattr(
            research_gaps, "plan_queries", lambda *a, **kw: _empty_plan()
        )
        monkeypatch.setattr(research_gaps, "search", lambda *a, **kw: [])
        monkeypatch.setattr(
            research_gaps,
            "batch_categorize_gaps",
            _stub_batch_categorize("open_ended"),
        )
        with caplog.at_level(
            "WARNING", logger="app.agents.research_gaps.research_gaps"
        ):
            research_gaps.run(same, article_id="TEST-001", budget=5)
        assert any("cross-family verification" in r.message for r in caplog.records)

    def test_conceptual_refused_by_answerer_writes_discussion_marker(
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
        article: Path,
    ) -> None:
        """When the answerer returns is_answered=False inside _ai_path,
        the gap is rendered as a discussion prompt and the verifier is
        NEVER called."""
        called: dict[str, int] = {"verify": 0}

        def ver(_s, **kw):
            called["verify"] += 1
            return VerifierVerdict(verdict="agree", confidence="high", issue="")

        monkeypatch.setattr(
            research_gaps,
            "batch_categorize_gaps",
            _stub_batch_categorize("conceptual"),
        )
        monkeypatch.setattr(
            research_gaps,
            "ai_answer_gap",
            _stub_answer(answered=False),
        )
        monkeypatch.setattr(research_gaps, "verify_ai_answer", ver)
        monkeypatch.setattr(
            research_gaps, "plan_queries", lambda *a, **kw: _empty_plan()
        )
        monkeypatch.setattr(research_gaps, "search", lambda *a, **kw: [])

        research_gaps.run(settings, article_id="TEST-001", budget=20)
        body = article.read_text(encoding="utf-8")
        # Verifier is never called when answerer refused
        assert called["verify"] == 0
        # Body shows the discussion marker for the refused gap
        assert "*(open-ended discussion prompt" in body

    def test_local_cultural_falls_back_to_ai_when_no_factual_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
        article: Path,
    ) -> None:
        """local_cultural tries factual first; if no source, falls
        through to the AI answerer + verifier path."""
        called: dict[str, int] = {"answer": 0, "verify": 0}

        monkeypatch.setattr(
            research_gaps,
            "batch_categorize_gaps",
            _stub_batch_categorize("local_cultural"),
        )

        def ans(_s, **kw):
            called["answer"] += 1
            return AIAnswer(
                answer="APECO is a Philippine special economic zone.",
                grounded_in="general_knowledge",
                is_answered=True,
                reasoning="ok",
            )

        def ver(_s, **kw):
            called["verify"] += 1
            return VerifierVerdict(verdict="agree", confidence="high", issue="")

        monkeypatch.setattr(research_gaps, "ai_answer_gap", ans)
        monkeypatch.setattr(research_gaps, "verify_ai_answer", ver)
        # Factual returns nothing — no query plan, no sources.
        monkeypatch.setattr(
            research_gaps, "plan_queries", lambda *a, **kw: _empty_plan()
        )
        monkeypatch.setattr(research_gaps, "search", lambda *a, **kw: [])

        research_gaps.run(settings, article_id="TEST-001", budget=20)
        # AI fallback fired for all 3 local_cultural gaps
        assert called["answer"] >= 1
        assert called["verify"] >= 1
        body = article.read_text(encoding="utf-8")
        assert "AI synthesis" in body


class TestBatchCategorizerAndParallelism:
    def test_batch_categorizer_used_for_per_article_categorization(
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
        article: Path,
    ) -> None:
        """batch_categorize_gaps is called exactly once per article;
        the per-gap categorize_gap is NOT called."""
        called: dict[str, int] = {"batch_categorize": 0, "per_gap_categorize": 0}

        def batch_cat(_s, questions, _summary):
            called["batch_categorize"] += 1
            return [
                GapCategorization(category="open_ended", reasoning="stub")
                for _ in questions
            ]

        def per_gap_cat(*_a, **_kw):
            called["per_gap_categorize"] += 1
            return GapCategorization(category="open_ended", reasoning="x")

        monkeypatch.setattr(research_gaps, "batch_categorize_gaps", batch_cat)
        monkeypatch.setattr(research_gaps, "categorize_gap", per_gap_cat)
        monkeypatch.setattr(
            research_gaps, "plan_queries", lambda *a, **kw: _empty_plan()
        )
        monkeypatch.setattr(research_gaps, "search", lambda *a, **kw: [])

        research_gaps.run(settings, article_id="TEST-001", budget=20)
        assert called["batch_categorize"] == 1
        assert called["per_gap_categorize"] == 0

    def test_gap_parallelism_setting_respected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        article: Path,
    ) -> None:
        """ThreadPoolExecutor is constructed with max_workers=GAP_PARALLELISM."""
        seen_max_workers: list[int] = []

        # Import the real executor so we can wrap it.
        from concurrent.futures import ThreadPoolExecutor as _RealExecutor

        class _ProbeExecutor:
            def __init__(self, max_workers: int) -> None:
                seen_max_workers.append(max_workers)
                self._inner = _RealExecutor(max_workers=max_workers)

            def __enter__(self):
                self._inner.__enter__()
                return self

            def __exit__(self, *exc):
                return self._inner.__exit__(*exc)

            def submit(self, fn, *args, **kwargs):
                return self._inner.submit(fn, *args, **kwargs)

        monkeypatch.setattr(research_gaps, "ThreadPoolExecutor", _ProbeExecutor)
        monkeypatch.setattr(
            research_gaps,
            "batch_categorize_gaps",
            _stub_batch_categorize("open_ended"),
        )
        monkeypatch.setattr(
            research_gaps, "plan_queries", lambda *a, **kw: _empty_plan()
        )
        monkeypatch.setattr(research_gaps, "search", lambda *a, **kw: [])

        # Run 1: GAP_PARALLELISM = 1
        s1 = Settings(
            CURATED_DIR=article.parent,
            AI_ANSWERER_MODEL="opus-4.7",
            AI_VERIFIER_MODEL="gemini-pro",
            GAP_CATEGORIZER_MODEL="gemini-flash",
            LOGS_DIR=str(tmp_path / "logs1"),
            GAP_PARALLELISM=1,
        )
        research_gaps.run(s1, article_id="TEST-001", budget=20)

        # Rewrite article so the second run also has gaps to process
        _write_article(article)

        # Run 2: GAP_PARALLELISM = 4
        s4 = Settings(
            CURATED_DIR=article.parent,
            AI_ANSWERER_MODEL="opus-4.7",
            AI_VERIFIER_MODEL="gemini-pro",
            GAP_CATEGORIZER_MODEL="gemini-flash",
            LOGS_DIR=str(tmp_path / "logs4"),
            GAP_PARALLELISM=4,
        )
        research_gaps.run(s4, article_id="TEST-001", budget=20)

        assert seen_max_workers == [1, 4]
