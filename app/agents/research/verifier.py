"""Research verifier — classifies findings and judges disputes.

Owns the two LLM agents that decide what to do with each external source:

- :func:`classify_finding` — classifies a source against a claim as
  ``confirm``, ``append``, or ``dispute``.
- :func:`judge_dispute` — second-pass check that a flagged dispute is a
  genuine factual disagreement, not a phrasing/scope difference.

Was ``executor.py`` in the previous layout. Renamed and slimmed during
the 2026-04-12 research restructure: the markdown-mutation function
``apply_findings_to_article`` moved to :mod:`app.agents.research.writer`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.agents.research.planner import ClaimEntry
from app.config.config import Settings
from app.core.llm.resolve import resolve_model

logger = logging.getLogger(__name__)

_VERIFIER_PROMPT_PATH = Path(__file__).parent / "prompts" / "verifier.md"
_JUDGE_PROMPT_PATH = Path(__file__).parent / "prompts" / "dispute_judge.md"


class FindingResult(BaseModel):
    """Classification of one external source finding against a claim."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["confirm", "append", "dispute"] = Field(
        ..., description="How this finding relates to the claim."
    )
    reasoning: str = Field(..., description="Why this classification.")
    evidence_quote: str = Field(..., description="Specific passage from the source.")
    new_sentence: str | None = Field(
        default=None, description="New sentence to append (append outcome only)."
    )
    contradiction: str | None = Field(
        default=None, description="Description of contradiction (dispute outcome only)."
    )


class DisputeJudgment(BaseModel):
    """Whether a flagged dispute is genuine or superficial."""

    model_config = ConfigDict(extra="forbid")

    is_genuine: bool = Field(..., description="True if real contradiction.")
    reasoning: str = Field(..., description="Explanation.")
    summary: str = Field(default="", description="Concise dispute description if genuine.")


# Type alias for a finding tuple: (claim, finding, source_title, source_url)
FindingTuple = tuple[ClaimEntry, FindingResult, str, str]


@dataclass
class ExecutorDeps:
    settings: Settings
    claim_text: str
    claim_section: str
    source_title: str
    source_content: str


@dataclass
class JudgeDeps:
    settings: Settings
    claim: str
    source_content: str
    initial_reasoning: str


def _build_verifier_agent(settings: Settings) -> Agent[ExecutorDeps, FindingResult]:
    return Agent(
        model=resolve_model(settings.RESEARCH_EXECUTOR_MODEL),
        deps_type=ExecutorDeps,
        output_type=FindingResult,
        system_prompt=_VERIFIER_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def _build_judge_agent(settings: Settings) -> Agent[JudgeDeps, DisputeJudgment]:
    return Agent(
        model=resolve_model(settings.RESEARCH_JUDGE_MODEL),
        deps_type=JudgeDeps,
        output_type=DisputeJudgment,
        system_prompt=_JUDGE_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def classify_finding(
    settings: Settings,
    claim: ClaimEntry,
    source_title: str,
    source_content: str,
    *,
    agent: Agent[ExecutorDeps, FindingResult] | None = None,
) -> FindingResult:
    """Classify a single source against a single claim."""
    agent = agent or _build_verifier_agent(settings)
    deps = ExecutorDeps(
        settings=settings,
        claim_text=claim.text,
        claim_section=claim.section,
        source_title=source_title,
        source_content=source_content,
    )
    user = (
        f"claim: {claim.text}\n"
        f"claim_section: {claim.section}\n"
        f"source_title: {source_title}\n\n"
        f"source_content:\n{source_content[:8000]}"
    )
    logger.debug(
        "classify input — claim: %r | source: %r",
        claim.text[:80],
        source_title,
    )
    result = agent.run_sync(user, deps=deps).output
    logger.debug(
        "classify output — outcome=%s | reasoning: %s",
        result.outcome,
        result.reasoning[:120],
    )
    return result


def judge_dispute(
    settings: Settings,
    claim: ClaimEntry,
    finding: FindingResult,
    source_content: str,
    *,
    agent: Agent[JudgeDeps, DisputeJudgment] | None = None,
) -> DisputeJudgment:
    """Determine if a flagged dispute is genuine."""
    agent = agent or _build_judge_agent(settings)
    deps = JudgeDeps(
        settings=settings,
        claim=claim.text,
        source_content=source_content,
        initial_reasoning=finding.reasoning,
    )
    user = (
        f"claim: {claim.text}\n"
        f"initial_reasoning: {finding.reasoning}\n"
        f"evidence_quote: {finding.evidence_quote}\n\n"
        f"source_content:\n{source_content[:4000]}"
    )
    logger.debug(
        "judge input — claim: %r | reasoning: %r | evidence: %r",
        claim.text[:80],
        finding.reasoning[:80],
        finding.evidence_quote[:80],
    )
    judgment = agent.run_sync(user, deps=deps).output
    logger.info(
        "judge output — genuine=%s | reasoning: %s | summary: %s",
        judgment.is_genuine,
        judgment.reasoning[:120],
        judgment.summary[:120] if judgment.summary else "(none)",
    )
    return judgment


__all__ = [
    "DisputeJudgment",
    "ExecutorDeps",
    "FindingResult",
    "FindingTuple",
    "JudgeDeps",
    "classify_finding",
    "judge_dispute",
]
