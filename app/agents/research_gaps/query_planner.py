"""Research-gaps query planner — gap questions → search queries.

Stage 1 of the research-gaps agent. Unlike the claim-verification planner,
this stage has no claim-extraction step: gap questions are already
structured items, so the planner only needs to generate search queries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.config.config import Settings
from app.core.llm.resolve import resolve_model

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "query_planner.md"


class GapQuery(BaseModel):
    """One search query targeting a specific gap question."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., description="The search string.")
    adapter: str = Field(..., description="Adapter name: wikipedia or tavily.")
    target_gap_idx: int = Field(
        ..., ge=0, description="Index into the gap_questions list."
    )


class GapQueryPlan(BaseModel):
    """Output of the gap query planner."""

    model_config = ConfigDict(extra="forbid")

    queries: list[GapQuery] = Field(..., description="Search queries to execute.")


@dataclass
class QueryPlannerDeps:
    """Runtime context for the gap query planner agent."""

    settings: Settings
    article_name: str
    gap_questions: list[str]
    available_adapters: list[str]
    budget_hint: int


def _build_planner_agent(settings: Settings) -> Agent[QueryPlannerDeps, GapQueryPlan]:
    return Agent(
        model=resolve_model(settings.RESEARCH_PLANNER_MODEL),
        deps_type=QueryPlannerDeps,
        output_type=GapQueryPlan,
        system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def plan_queries(
    settings: Settings,
    deps: QueryPlannerDeps,
    *,
    agent: Agent[QueryPlannerDeps, GapQueryPlan] | None = None,
) -> GapQueryPlan:
    """Run the gap query planner and return a plan of searches."""
    agent = agent or _build_planner_agent(settings)
    parts = [
        f"article_name: {deps.article_name}",
        f"available_adapters: {deps.available_adapters}",
        f"budget_hint: {deps.budget_hint}",
        "gap_questions:",
        *[f"  [{i}] {q}" for i, q in enumerate(deps.gap_questions)],
    ]
    user = "\n".join(parts)
    return agent.run_sync(user, deps=deps).output


__all__ = ["GapQuery", "GapQueryPlan", "QueryPlannerDeps", "plan_queries"]
