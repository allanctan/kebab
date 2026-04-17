"""Batch verifier — one-shot verification of all claims against all sources.

Replaces the per-claim-per-source loop in :mod:`app.agents.research.research`
with a single LLM call that receives all claims and all source content at once,
returning one :class:`BatchFinding` per claim.

Use :func:`batch_findings_to_tuples` to convert the findings to the
:data:`~app.agents.research.verifier.FindingTuple` format expected by
:func:`~app.agents.research.writer.apply_findings_to_article`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai import Agent

from app.agents.research.planner import ClaimEntry
from app.agents.research.verifier import (
    SURFACED_CATEGORIES,
    DisputeCategory,
    FindingResult,
    FindingTuple,
)
from app.config.config import Settings
from app.core.llm.resolve import resolve_model
from app.core.research.searcher import SourceContent

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "batch_verifier.md"


class BatchFinding(BaseModel):
    """One finding for a single claim, produced by the batch verifier."""

    model_config = ConfigDict(extra="forbid")

    claim_idx: int = Field(..., description="Index into the claims list.")
    outcome: Literal["confirm", "append", "dispute", "unverified"] = Field(
        ..., description="How this finding relates to the claim."
    )
    source_title: str = Field(
        default="", description="Title of the source that produced this finding."
    )
    source_url: str = Field(
        default="", description="URL of the source that produced this finding."
    )
    evidence_quote: str = Field(
        default="", description="Verbatim passage from the source."
    )
    new_sentence: str | None = Field(
        default=None, description="New sentence to append (append outcome only)."
    )
    contradiction: str | None = Field(
        default=None, description="Description of contradiction (dispute outcome only)."
    )
    dispute_category: DisputeCategory | None = Field(
        default=None,
        description="Dispute category (dispute outcome only). "
        "One of: factual_error, misleading_simplification, contested_or_opinion, "
        "acceptable_simplification, false_positive.",
    )
    reasoning: str = Field(..., description="Why this classification was made.")

    @field_validator("outcome", mode="before")
    @classmethod
    def _normalize_outcome(cls, v: object) -> object:
        """Normalize LLM output — 'Confirm', 'APPEND', etc. → canonical form."""
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("dispute_category", mode="before")
    @classmethod
    def _normalize_dispute_category(cls, v: object) -> object:
        """Normalize LLM output — 'Factual Error', 'FACTUAL_ERROR' → 'factual_error'."""
        if isinstance(v, str):
            return v.strip().lower().replace(" ", "_").replace("-", "_")
        return v


class BatchVerifyResult(BaseModel):
    """Output of the batch verifier agent — one finding per claim."""

    model_config = ConfigDict(extra="forbid")

    findings: list[BatchFinding] = Field(
        ..., description="One BatchFinding per claim, in any order."
    )


@dataclass
class BatchVerifierDeps:
    """Runtime context passed to the batch verifier agent."""

    settings: Settings
    article_name: str
    article_body: str
    claims: list[ClaimEntry]
    sources: list[SourceContent]
    authoritative_domains: list[str]


def batch_findings_to_tuples(
    findings: list[BatchFinding],
    claims: list[ClaimEntry],
) -> list[FindingTuple]:
    """Convert :class:`BatchFinding` list to :data:`~app.agents.research.verifier.FindingTuple` list.

    Skips findings that should not reach the writer:
    - ``unverified`` outcomes (no source addressed the claim)
    - Out-of-range ``claim_idx`` values
    - Disputes with ``false_positive`` or ``acceptable_simplification`` category
      (not in :data:`~app.agents.research.verifier.SURFACED_CATEGORIES`)
    """
    tuples: list[FindingTuple] = []

    for finding in findings:
        if finding.outcome == "unverified":
            continue

        if finding.claim_idx < 0 or finding.claim_idx >= len(claims):
            logger.warning(
                "batch_verifier: claim_idx %d out of range (len=%d) — skipping",
                finding.claim_idx,
                len(claims),
            )
            continue

        if finding.outcome == "dispute" and finding.dispute_category is not None:
            if finding.dispute_category not in SURFACED_CATEGORIES:
                logger.debug(
                    "batch_verifier: suppressing dispute category=%s for claim_idx=%d",
                    finding.dispute_category,
                    finding.claim_idx,
                )
                continue

        claim = claims[finding.claim_idx]
        finding_result = FindingResult(
            outcome=finding.outcome,  # type: ignore[arg-type]
            reasoning=finding.reasoning,
            evidence_quote=finding.evidence_quote,
            new_sentence=finding.new_sentence,
            contradiction=finding.contradiction,
            dispute_category=finding.dispute_category,
        )
        tuples.append((claim, finding_result, finding.source_title, finding.source_url))

    return tuples


def _build_batch_prompt(deps: BatchVerifierDeps) -> str:
    """Assemble the user-turn prompt for the batch verifier agent.

    Sections:
    1. Article name + body
    2. Numbered claims list
    3. Sources (title, URL, content[:4000])
    4. Authoritative domains (first 20)
    """
    parts: list[str] = []

    # Article
    parts.append(f"## Article: {deps.article_name}")
    parts.append("")
    parts.append(deps.article_body)
    parts.append("")

    # Claims
    parts.append("## Claims to Verify")
    parts.append("")
    for idx, claim in enumerate(deps.claims):
        parts.append(
            f'{idx}. "{claim.text}" (section: {claim.section}, paragraph: {claim.paragraph})'
        )
    parts.append("")

    # Sources
    parts.append("## Sources")
    parts.append("")
    for src in deps.sources:
        parts.append(f"### {src.title} ({src.url})")
        parts.append(src.content[:4000])
        parts.append("")

    # Authoritative domains
    if deps.authoritative_domains:
        parts.append("## Authoritative Domains")
        parts.append("")
        for domain in deps.authoritative_domains[:20]:
            parts.append(f"- {domain}")
        parts.append("")

    return "\n".join(parts)


def batch_verify(
    settings: Settings,
    deps: BatchVerifierDeps,
    *,
    agent: Agent[BatchVerifierDeps, BatchVerifyResult] | None = None,
) -> list[BatchFinding]:
    """Run the batch verifier agent and return one finding per claim.

    Args:
        settings:  KEBAB runtime configuration.
        deps:      Runtime context (article, claims, sources, authoritative domains).
        agent:     Optional pre-built agent for testing. When ``None``, a fresh
                   agent is constructed from ``settings.RESEARCH_EXECUTOR_MODEL``.

    Returns:
        List of :class:`BatchFinding` — one per claim in the input.
    """
    if agent is None:
        agent = Agent(
            model=resolve_model(settings.RESEARCH_EXECUTOR_MODEL),
            deps_type=BatchVerifierDeps,
            output_type=BatchVerifyResult,
            system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
            retries=settings.LLM_MAX_RETRIES,
        )

    user = _build_batch_prompt(deps)
    logger.debug(
        "batch_verify: %d claims × %d sources for %r",
        len(deps.claims),
        len(deps.sources),
        deps.article_name,
    )
    result = agent.run_sync(user, deps=deps).output
    logger.info(
        "batch_verify: received %d findings for %r",
        len(result.findings),
        deps.article_name,
    )
    return result.findings


__all__ = [
    "BatchFinding",
    "BatchVerifierDeps",
    "BatchVerifyResult",
    "batch_findings_to_tuples",
    "batch_verify",
]
