"""Research-gaps AI answerer — Opus-driven synthesis for non-factual gaps.

Generates a short, defensible, stand-alone answer for conceptual /
pedagogical / local_cultural-fallback questions. The answer is later
verified cross-family by :mod:`app.agents.research_gaps.ai_verifier`
before it can be written to the article.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai import Agent

from app.agents.research_gaps.categorizer import GapCategory
from app.config.config import Settings
from app.core.llm.resolve import resolve_model

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "ai_answerer.md"

Grounding = Literal["article_body", "general_knowledge"]


class AIAnswer(BaseModel):
    """One AI-synthesized answer (or refusal)."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(
        ...,
        description=(
            "Short, defensible, stand-alone answer (1–3 sentences, ≤ 60 "
            "words). Empty if not answerable."
        ),
    )
    grounded_in: Grounding = Field(..., description="Where the answer is grounded.")
    is_answered: bool = Field(
        ..., description="False for genuinely open-ended questions."
    )
    reasoning: str = Field(..., description="Brief justification (audit log only).")

    @field_validator("grounded_in", mode="before")
    @classmethod
    def _normalize_grounding(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower().replace(" ", "_").replace("-", "_")
        return v


@dataclass
class AnswererDeps:
    settings: Settings
    question: str
    article_body: str
    article_summary: str
    category: GapCategory


def _default_answerer_agent(settings: Settings) -> Agent[AnswererDeps, AIAnswer]:
    return Agent(
        model=resolve_model(settings.AI_ANSWERER_MODEL),
        deps_type=AnswererDeps,
        output_type=AIAnswer,
        system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def ai_answer_gap(
    settings: Settings,
    *,
    question: str,
    article_body: str,
    article_summary: str,
    category: GapCategory,
    agent: Agent[AnswererDeps, AIAnswer] | None = None,
) -> AIAnswer:
    """One LLM call → grounded answer or refusal."""
    agent = agent or _default_answerer_agent(settings)
    deps = AnswererDeps(
        settings=settings,
        question=question,
        article_body=article_body,
        article_summary=article_summary,
        category=category,
    )
    # Article body is truncated to keep the prompt within budget. Real
    # articles run 5–10k chars; 16k leaves a comfortable margin.
    body_for_prompt = article_body[:16000]
    user = (
        f"question: {question}\n"
        f"category: {category}\n"
        f"article_summary: {article_summary}\n\n"
        f"article_body:\n{body_for_prompt}"
    )
    result = agent.run_sync(user, deps=deps).output
    logger.debug(
        "ai_answer_gap: %r → answered=%s grounded=%s",
        question[:80],
        result.is_answered,
        result.grounded_in,
    )
    return result


__all__ = ["AIAnswer", "AnswererDeps", "Grounding", "ai_answer_gap"]
