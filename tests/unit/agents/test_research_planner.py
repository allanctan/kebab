"""Tests for the research planner agent."""

from __future__ import annotations

from app.agents.research.planner import (
    ClaimEntry,
    ResearchPlan,
    SearchQuery,
    PlannerDeps,
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
