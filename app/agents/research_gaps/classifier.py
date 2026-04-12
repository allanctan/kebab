"""Research-gaps classifier — does this source answer the question?

Stage 2 of the research-gaps agent. A thinner cousin of
:mod:`app.agents.research.verifier.classify_finding`: there's no
"confirm/append/dispute" axis here because gaps are open questions, not
existing claims. The classifier returns a yes/no plus a concise grounded
answer.
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

_PROMPT_PATH = Path(__file__).parent / "prompts" / "classifier.md"


class GapClassification(BaseModel):
    """Result of evaluating a source against an open question."""

    model_config = ConfigDict(extra="forbid")

    is_answered: bool = Field(
        ..., description="True if source provides a clear answer to the question."
    )
    answer: str = Field(
        default="",
        description="Concise 1–2 sentence answer (empty if not answered).",
    )
    reasoning: str = Field(..., description="Brief explanation of the judgment.")


@dataclass
class ClassifierDeps:
    settings: Settings
    question: str
    source_title: str
    source_content: str


def _build_classifier_agent(settings: Settings) -> Agent[ClassifierDeps, GapClassification]:
    return Agent(
        model=resolve_model(settings.RESEARCH_EXECUTOR_MODEL),
        deps_type=ClassifierDeps,
        output_type=GapClassification,
        system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def answer_question(
    settings: Settings,
    *,
    question: str,
    source_title: str,
    source_content: str,
    agent: Agent[ClassifierDeps, GapClassification] | None = None,
) -> GapClassification:
    """Decide whether ``source_content`` answers ``question``."""
    agent = agent or _build_classifier_agent(settings)
    deps = ClassifierDeps(
        settings=settings,
        question=question,
        source_title=source_title,
        source_content=source_content,
    )
    user = (
        f"question: {question}\n"
        f"source_title: {source_title}\n\n"
        f"source_content:\n{source_content[:8000]}"
    )
    logger.debug("classify gap input — question: %r | source: %r", question[:80], source_title)
    result = agent.run_sync(user, deps=deps).output
    logger.debug(
        "classify gap output — answered=%s | reasoning: %s",
        result.is_answered,
        result.reasoning[:120],
    )
    return result


__all__ = ["ClassifierDeps", "GapClassification", "answer_question"]
