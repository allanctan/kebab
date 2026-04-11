"""Research planner — extracts claims and generates search queries.

Stage 1 of the research agent. Reads an article body and produces
a structured research plan: what to search for, where, and which
claims each query targets.
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

_PROMPT_PATH = Path(__file__).parent / "prompts" / "planner.md"


class ClaimEntry(BaseModel):
    """One factual claim extracted from the article."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., description="The claim statement.")
    section: str = Field(..., description="Markdown section heading.")
    paragraph: int = Field(..., ge=0, description="Paragraph number within the section (0 for gap-derived claims).")


class SearchQuery(BaseModel):
    """One search query targeting specific claims."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., description="The search string.")
    adapter: str = Field(..., description="Adapter name: wikipedia, openstax, or tavily.")
    target_claims: list[int] = Field(..., description="Indices of claims this query aims to verify.")


class ResearchPlan(BaseModel):
    """Output of the planner agent."""

    model_config = ConfigDict(extra="forbid")

    claims: list[ClaimEntry] = Field(..., description="Extracted factual claims.")
    queries: list[SearchQuery] = Field(..., description="Search queries to execute.")


@dataclass
class PlannerDeps:
    """Runtime context for the planner agent."""

    settings: Settings
    article_name: str
    article_body: str
    available_adapters: list[str]
    budget_hint: int
    research_gaps: list[str]


def _build_planner_agent(settings: Settings) -> Agent[PlannerDeps, ResearchPlan]:
    return Agent(
        model=resolve_model(settings.RESEARCH_PLANNER_MODEL),
        deps_type=PlannerDeps,
        output_type=ResearchPlan,
        system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def plan_research(
    settings: Settings,
    deps: PlannerDeps,
    *,
    agent: Agent[PlannerDeps, ResearchPlan] | None = None,
) -> ResearchPlan:
    """Run the planner agent and return a research plan."""
    agent = agent or _build_planner_agent(settings)
    parts = [
        f"article_name: {deps.article_name}",
        f"available_adapters: {deps.available_adapters}",
        f"budget_hint: {deps.budget_hint}",
    ]
    if deps.research_gaps:
        parts.append("research_gaps:\n" + "\n".join(f"- {g}" for g in deps.research_gaps))
    parts.append(f"\narticle_body:\n{deps.article_body}")
    user = "\n".join(parts)
    return agent.run_sync(user, deps=deps).output
