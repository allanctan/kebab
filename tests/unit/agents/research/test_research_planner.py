"""Tests for the research planner agent."""

from __future__ import annotations

import pytest

from app.agents.research.planner import (
    ClaimEntry,
    ResearchPlan,
    SearchQuery,
    PlannerDeps,
    plan_research,
)
from app.agents.research.research import (
    _extract_confirmed_urls,
    _extract_gap_answers,
)


def _stub_plan() -> ResearchPlan:
    return ResearchPlan(
        claims=[
            ClaimEntry(text="Plates move due to convection", section="Causes", paragraph=1),
            ClaimEntry(text="Slab pull is a mechanism", section="Causes", paragraph=2),
        ],
        queries=[
            SearchQuery(query="plate tectonics convection", adapter="wikipedia", target_claims=[0]),
            SearchQuery(query="slab pull mechanism", adapter="openstax", target_claims=[1]),
        ],
    )


class TestResearchPlanModels:
    def test_plan_has_claims_and_queries(self) -> None:
        plan = _stub_plan()
        assert len(plan.claims) == 2
        assert len(plan.queries) == 2

    def test_each_claim_has_text_and_location(self) -> None:
        plan = _stub_plan()
        claim = plan.claims[0]
        assert claim.text == "Plates move due to convection"
        assert claim.section == "Causes"
        assert claim.paragraph == 1

    def test_each_query_targets_claims(self) -> None:
        plan = _stub_plan()
        q = plan.queries[0]
        assert q.adapter == "wikipedia"
        assert 0 in q.target_claims

    def test_claim_entry_validates(self) -> None:
        entry = ClaimEntry(text="test", section="Intro", paragraph=1)
        assert entry.text == "test"

    def test_search_query_validates(self) -> None:
        q = SearchQuery(query="test", adapter="wikipedia", target_claims=[0])
        assert q.adapter == "wikipedia"

    def test_planner_deps_dataclass(self) -> None:
        deps = PlannerDeps(
            settings=None,
            article_name="Test",
            article_body="body",
            available_adapters=["wikipedia"],
            budget_hint=5,
        )
        assert deps.article_name == "Test"
        assert deps.budget_hint == 5

    def test_planner_deps_new_fields_default_to_empty(self) -> None:
        deps = PlannerDeps(
            settings=None,
            article_name="Test",
            article_body="body",
            available_adapters=["wikipedia"],
            budget_hint=5,
        )
        assert deps.confirmed_footnote_urls == []
        assert deps.gap_answers == []

    def test_planner_deps_accepts_new_fields(self) -> None:
        deps = PlannerDeps(
            settings=None,
            article_name="Test",
            article_body="body",
            available_adapters=["wikipedia"],
            budget_hint=5,
            confirmed_footnote_urls=["https://example.com/source"],
            gap_answers=["The answer is 42."],
        )
        assert deps.confirmed_footnote_urls == ["https://example.com/source"]
        assert deps.gap_answers == ["The answer is 42."]


class TestExtractConfirmedUrls:
    def test_extracts_url_from_footnote(self) -> None:
        body = "[^1]: [Wikipedia](https://en.wikipedia.org/wiki/Plate_tectonics)"
        urls = _extract_confirmed_urls(body)
        assert urls == ["https://en.wikipedia.org/wiki/Plate_tectonics"]

    def test_extracts_multiple_urls(self) -> None:
        body = (
            "[^1]: [Source A](https://example.com/a)\n"
            "[^2]: [Source B](https://example.com/b)\n"
        )
        urls = _extract_confirmed_urls(body)
        assert urls == ["https://example.com/a", "https://example.com/b"]

    def test_ignores_non_footnote_links(self) -> None:
        body = "See [this link](https://example.com) for details.\n"
        urls = _extract_confirmed_urls(body)
        assert urls == []

    def test_returns_empty_list_when_no_footnotes(self) -> None:
        body = "No footnotes here at all."
        urls = _extract_confirmed_urls(body)
        assert urls == []

    def test_ignores_footnotes_without_url(self) -> None:
        body = "[^1]: Plain text reference without a link."
        urls = _extract_confirmed_urls(body)
        assert urls == []

    def test_handles_http_and_https(self) -> None:
        body = (
            "[^1]: [HTTP](http://example.com/page)\n"
            "[^2]: [HTTPS](https://secure.example.com/page)\n"
        )
        urls = _extract_confirmed_urls(body)
        assert "http://example.com/page" in urls
        assert "https://secure.example.com/page" in urls


class TestExtractGapAnswers:
    def test_extracts_answer_after_question(self) -> None:
        body = (
            "## Research Gaps\n"
            "**Q:** What causes plate tectonics?\n"
            "Convection currents in the mantle drive plate movement.\n"
        )
        answers = _extract_gap_answers(body)
        assert answers == ["Convection currents in the mantle drive plate movement."]

    def test_extracts_multiple_answers(self) -> None:
        body = (
            "## Research Gaps\n"
            "**Q:** Question one?\n"
            "Answer one.\n"
            "**Q:** Question two?\n"
            "Answer two.\n"
        )
        answers = _extract_gap_answers(body)
        assert answers == ["Answer one.", "Answer two."]

    def test_stops_at_next_section(self) -> None:
        body = (
            "## Research Gaps\n"
            "**Q:** What is slab pull?\n"
            "Slab pull is gravity acting on a dense sinking plate.\n"
            "## Other Section\n"
            "**Q:** Should not be included.\n"
            "This answer should not be captured.\n"
        )
        answers = _extract_gap_answers(body)
        assert answers == ["Slab pull is gravity acting on a dense sinking plate."]

    def test_returns_empty_when_no_gaps_section(self) -> None:
        body = "## Introduction\nSome content here.\n"
        answers = _extract_gap_answers(body)
        assert answers == []

    def test_returns_empty_when_gaps_section_has_no_qa(self) -> None:
        body = "## Research Gaps\nNo Q&A pairs here.\n"
        answers = _extract_gap_answers(body)
        assert answers == []

    def test_skips_blank_lines_between_question_and_answer(self) -> None:
        body = (
            "## Research Gaps\n"
            "**Q:** What is convection?\n"
            "\n"
            "Heat-driven circulation in the mantle.\n"
        )
        # blank line between Q and A — capture_next stays True until a
        # non-empty stripped line is found
        answers = _extract_gap_answers(body)
        assert answers == ["Heat-driven circulation in the mantle."]


class TestPlanResearchPromptContent:
    """Verify that plan_research builds the user prompt with the right sections."""

    def _make_deps(
        self,
        confirmed_footnote_urls: list[str] | None = None,
        gap_answers: list[str] | None = None,
    ) -> PlannerDeps:
        return PlannerDeps(
            settings=None,
            article_name="Test Article",
            article_body="Body text.",
            available_adapters=["wikipedia"],
            budget_hint=5,
            confirmed_footnote_urls=confirmed_footnote_urls or [],
            gap_answers=gap_answers or [],
        )

    def test_prompt_contains_article_name(self, mocker: pytest.fixture) -> None:
        captured: list[str] = []

        def fake_run_sync(prompt: str, *, deps: PlannerDeps) -> object:
            captured.append(prompt)

            class _FakeResult:
                output = _stub_plan()

            return _FakeResult()

        mock_agent = mocker.MagicMock()
        mock_agent.run_sync.side_effect = fake_run_sync
        deps = self._make_deps()
        plan_research(None, deps, agent=mock_agent)
        assert "article_name: Test Article" in captured[0]

    def test_prompt_excludes_confirmed_section_when_empty(self, mocker: pytest.fixture) -> None:
        captured: list[str] = []

        def fake_run_sync(prompt: str, *, deps: PlannerDeps) -> object:
            captured.append(prompt)

            class _FakeResult:
                output = _stub_plan()

            return _FakeResult()

        mock_agent = mocker.MagicMock()
        mock_agent.run_sync.side_effect = fake_run_sync
        deps = self._make_deps(confirmed_footnote_urls=[])
        plan_research(None, deps, agent=mock_agent)
        assert "Already confirmed" not in captured[0]

    def test_prompt_excludes_gap_answers_section_when_empty(self, mocker: pytest.fixture) -> None:
        captured: list[str] = []

        def fake_run_sync(prompt: str, *, deps: PlannerDeps) -> object:
            captured.append(prompt)

            class _FakeResult:
                output = _stub_plan()

            return _FakeResult()

        mock_agent = mocker.MagicMock()
        mock_agent.run_sync.side_effect = fake_run_sync
        deps = self._make_deps(gap_answers=[])
        plan_research(None, deps, agent=mock_agent)
        assert "Gap answers to verify" not in captured[0]

    def test_prompt_includes_confirmed_section_with_urls(self, mocker: pytest.fixture) -> None:
        captured: list[str] = []

        def fake_run_sync(prompt: str, *, deps: PlannerDeps) -> object:
            captured.append(prompt)

            class _FakeResult:
                output = _stub_plan()

            return _FakeResult()

        mock_agent = mocker.MagicMock()
        mock_agent.run_sync.side_effect = fake_run_sync
        deps = self._make_deps(confirmed_footnote_urls=["https://en.wikipedia.org/wiki/Foo"])
        plan_research(None, deps, agent=mock_agent)
        prompt = captured[0]
        assert "Already confirmed (skip these)" in prompt
        assert "- https://en.wikipedia.org/wiki/Foo" in prompt

    def test_prompt_includes_gap_answers_section_with_answers(self, mocker: pytest.fixture) -> None:
        captured: list[str] = []

        def fake_run_sync(prompt: str, *, deps: PlannerDeps) -> object:
            captured.append(prompt)

            class _FakeResult:
                output = _stub_plan()

            return _FakeResult()

        mock_agent = mocker.MagicMock()
        mock_agent.run_sync.side_effect = fake_run_sync
        deps = self._make_deps(gap_answers=["Convection drives plate tectonics."])
        plan_research(None, deps, agent=mock_agent)
        prompt = captured[0]
        assert "Gap answers to verify" in prompt
        assert "- Convection drives plate tectonics." in prompt

    def test_article_body_always_appears_last(self, mocker: pytest.fixture) -> None:
        captured: list[str] = []

        def fake_run_sync(prompt: str, *, deps: PlannerDeps) -> object:
            captured.append(prompt)

            class _FakeResult:
                output = _stub_plan()

            return _FakeResult()

        mock_agent = mocker.MagicMock()
        mock_agent.run_sync.side_effect = fake_run_sync
        deps = self._make_deps(
            confirmed_footnote_urls=["https://example.com/a"],
            gap_answers=["An answer."],
        )
        plan_research(None, deps, agent=mock_agent)
        prompt = captured[0]
        body_pos = prompt.index("article_body:")
        confirmed_pos = prompt.index("Already confirmed")
        gap_pos = prompt.index("Gap answers to verify")
        assert body_pos > confirmed_pos
        assert body_pos > gap_pos
