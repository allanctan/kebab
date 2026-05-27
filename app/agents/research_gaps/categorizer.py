"""Research-gaps categorizer — what kind of question is this?

Picks ONE of five categories so the orchestrator can route the question
to the right answering strategy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai import Agent

from app.config.config import Settings
from app.core.llm.resolve import resolve_model

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "categorizer.md"
_BATCH_PROMPT_PATH = Path(__file__).parent / "prompts" / "batch_categorizer.md"

GapCategory = Literal[
    "factual", "conceptual", "pedagogical", "open_ended", "local_cultural"
]


class GapCategorization(BaseModel):
    """One categorization decision."""

    model_config = ConfigDict(extra="forbid")

    category: GapCategory = Field(..., description="The question type.")
    reasoning: str = Field(..., description="One-sentence justification.")

    @field_validator("category", mode="before")
    @classmethod
    def _normalize(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower().replace(" ", "_").replace("-", "_")
        return v


@dataclass
class CategorizerDeps:
    settings: Settings
    question: str
    article_summary: str


def _default_categorizer_agent(
    settings: Settings,
) -> Agent[CategorizerDeps, GapCategorization]:
    return Agent(
        model=resolve_model(settings.GAP_CATEGORIZER_MODEL),
        deps_type=CategorizerDeps,
        output_type=GapCategorization,
        system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def categorize_gap(
    settings: Settings,
    question: str,
    article_summary: str,
    *,
    agent: Agent[CategorizerDeps, GapCategorization] | None = None,
) -> GapCategorization:
    """Single LLM call → category + reasoning."""
    agent = agent or _default_categorizer_agent(settings)
    deps = CategorizerDeps(
        settings=settings, question=question, article_summary=article_summary
    )
    user = f"question: {question}\n\narticle_summary: {article_summary}"
    result = agent.run_sync(user, deps=deps).output
    logger.debug(
        "categorize_gap: %r → %s (%s)",
        question[:80],
        result.category,
        result.reasoning[:80],
    )
    return result


class BatchGapCategorization(BaseModel):
    """Batch output — one ``GapCategorization`` per input question, in order."""

    model_config = ConfigDict(extra="forbid")

    categorizations: list[GapCategorization] = Field(
        ..., description="One categorization per input question, in the same order."
    )


@dataclass
class BatchCategorizerDeps:
    settings: Settings
    questions: list[str]
    article_summary: str


def _default_batch_categorizer_agent(
    settings: Settings,
) -> Agent[BatchCategorizerDeps, BatchGapCategorization]:
    return Agent(
        model=resolve_model(settings.GAP_CATEGORIZER_MODEL),
        deps_type=BatchCategorizerDeps,
        output_type=BatchGapCategorization,
        system_prompt=_BATCH_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def _build_batch_user_message(questions: list[str], article_summary: str) -> str:
    """Assemble the user-turn prompt for the batch categorizer agent."""
    parts: list[str] = []
    parts.append("article_summary: " + article_summary)
    parts.append("")
    parts.append("questions:")
    for idx, question in enumerate(questions, start=1):
        parts.append(f"{idx}. {question}")
    return "\n".join(parts)


def batch_categorize_gaps(
    settings: Settings,
    questions: list[str],
    article_summary: str,
    *,
    agent: Agent[BatchCategorizerDeps, BatchGapCategorization] | None = None,
) -> list[GapCategorization]:
    """One LLM call → N categorizations in the same order as ``questions``.

    If the agent returns fewer than N entries, the result is padded with a
    ``factual`` default (with a reasoning string explaining the pad) and a
    warning is logged. If the agent returns more than N entries, the result
    is truncated to N.
    """
    if not questions:
        return []

    agent = agent or _default_batch_categorizer_agent(settings)
    deps = BatchCategorizerDeps(
        settings=settings, questions=questions, article_summary=article_summary
    )
    user = _build_batch_user_message(questions, article_summary)
    result = agent.run_sync(user, deps=deps).output

    categorizations = list(result.categorizations)
    expected = len(questions)
    actual = len(categorizations)

    if actual < expected:
        missing = expected - actual
        logger.warning(
            "batch_categorize_gaps: agent returned %d categorizations for %d "
            "questions — padding %d with default 'factual'.",
            actual,
            expected,
            missing,
        )
        for _ in range(missing):
            categorizations.append(
                GapCategorization(
                    category="factual",
                    reasoning=(
                        "categorizer returned fewer entries — defaulting to factual"
                    ),
                )
            )
    elif actual > expected:
        logger.warning(
            "batch_categorize_gaps: agent returned %d categorizations for %d "
            "questions — truncating to %d.",
            actual,
            expected,
            expected,
        )
        categorizations = categorizations[:expected]

    logger.debug(
        "batch_categorize_gaps: %d questions → %d categorizations",
        expected,
        len(categorizations),
    )
    return categorizations


__all__ = [
    "BatchCategorizerDeps",
    "BatchGapCategorization",
    "CategorizerDeps",
    "GapCategorization",
    "GapCategory",
    "batch_categorize_gaps",
    "categorize_gap",
]
