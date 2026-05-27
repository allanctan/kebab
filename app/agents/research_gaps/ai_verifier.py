"""Research-gaps AI verifier — cross-family check before write.

A different model family (e.g. Gemini Pro) reviews the AI answerer's
proposed answer. If the verifier disagrees or has low confidence in an
agreement, the answer is suppressed and a short rejection note replaces
it in the article.
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

_PROMPT_PATH = Path(__file__).parent / "prompts" / "ai_verifier.md"

Verdict = Literal["agree", "disagree", "partial"]
Confidence = Literal["high", "medium", "low"]


class VerifierVerdict(BaseModel):
    """Cross-family judgment on a proposed AI answer."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict = Field(
        ...,
        description=(
            "agree = answer is defensible; disagree = factual issue; "
            "partial = answer addresses only part."
        ),
    )
    issue: str = Field(
        default="",
        description=(
            "If disagree/partial: one sentence on what's wrong. Empty if agree."
        ),
    )
    confidence: Confidence = Field(
        ..., description="Verifier's confidence in its own verdict."
    )

    @field_validator("verdict", "confidence", mode="before")
    @classmethod
    def _normalize(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower().replace(" ", "_").replace("-", "_")
        return v


@dataclass
class VerifierDeps:
    settings: Settings
    question: str
    proposed_answer: str
    article_summary: str


def _default_verifier_agent(
    settings: Settings,
) -> Agent[VerifierDeps, VerifierVerdict]:
    return Agent(
        model=resolve_model(settings.AI_VERIFIER_MODEL),
        deps_type=VerifierDeps,
        output_type=VerifierVerdict,
        system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def verify_ai_answer(
    settings: Settings,
    *,
    question: str,
    answer: str,
    article_summary: str,
    agent: Agent[VerifierDeps, VerifierVerdict] | None = None,
) -> VerifierVerdict:
    """Cross-family verification. Verifier does NOT see the article body.

    Args:
        question: the research-gap question.
        answer: the proposed AI answer to check.
        article_summary: 1–2 sentence summary for context only.
    """
    agent = agent or _default_verifier_agent(settings)
    deps = VerifierDeps(
        settings=settings,
        question=question,
        proposed_answer=answer,
        article_summary=article_summary,
    )
    user = (
        f"question: {question}\n"
        f"article_summary: {article_summary}\n\n"
        f"proposed_answer: {answer}"
    )
    result = agent.run_sync(user, deps=deps).output
    logger.debug(
        "verify_ai_answer: %r → %s/%s (%s)",
        question[:80],
        result.verdict,
        result.confidence,
        result.issue[:80],
    )
    return result


def verify_passes(v: VerifierVerdict) -> bool:
    """The orchestrator gate.

    Writes the answer iff:
      * verdict == agree AND confidence in {medium, high}, OR
      * verdict == partial AND confidence == high.

    All other combinations suppress the answer; the orchestrator renders
    a short italic rejection note in its place.
    """
    if v.verdict == "agree" and v.confidence in ("medium", "high"):
        return True
    if v.verdict == "partial" and v.confidence == "high":
        return True
    return False


__all__ = [
    "Confidence",
    "Verdict",
    "VerifierDeps",
    "VerifierVerdict",
    "verify_ai_answer",
    "verify_passes",
]
