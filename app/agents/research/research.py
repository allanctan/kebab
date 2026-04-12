"""Research agent — claim verification orchestrator.

Loads an article, runs the planner, executes the search plan via the
shared :mod:`app.core.research.searcher`, classifies each finding via the
verifier (with dispute judging for genuine contradictions), applies the
findings to the article body via the writer, and updates frontmatter
with the run's metadata.

Replaces the previous ``app/agents/research/agent.py`` from the
2026-04-12 research restructure. Compared to the old orchestrator:

- The ``mode="all"|"content"|"gaps"`` parameter is gone — gap-answering
  moved to :mod:`app.agents.research_gaps`.
- The ``planner=``, ``searcher=``, ``classifier=`` callable swap-points
  are gone — tests inject at the per-step ``agent=`` overrides instead.
- All Wikipedia image enrichment is gone — moved to
  :mod:`app.agents.research_images`.
"""

from __future__ import annotations

import logging
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.agents.research.planner import (
    ClaimEntry,
    PlannerDeps,
    ResearchPlan,
    plan_research,
)
from app.agents.research.synthesizer import merge_appends
from app.agents.research.verifier import (
    FindingResult,
    FindingTuple,
    classify_finding,
    judge_dispute,
)
from app.agents.research.writer import apply_findings_to_article
from app.config.config import Settings
from app.core.audit import log_event
from app.core.markdown import (
    count_external_footnotes,
    extract_disputes,
    find_article_by_id,
    next_footnote_number,
    parse_body,
    read_article,
    write_article,
)
from app.core.research.searcher import search

logger = logging.getLogger(__name__)


class ResearchResult(BaseModel):
    """Summary of one research run."""

    model_config = ConfigDict(extra="forbid")

    article_id: str = Field(..., description="ID of the researched article.")
    claims_total: int = Field(default=0, description="Number of claims extracted.")
    confirms: int = Field(default=0, description="Number of confirmed claims.")
    appends: int = Field(default=0, description="Number of appended facts.")
    disputes: int = Field(default=0, description="Number of genuine disputes found.")
    findings: list[str] = Field(
        default_factory=list,
        description="Human-readable summary of each finding.",
    )


def _available_adapters(settings: Settings) -> list[str]:
    """Return verification-capable adapters available given current credentials."""
    adapters = ["wikipedia"]
    if getattr(settings, "TAVILY_API_KEY", ""):
        adapters.append("tavily")
    return adapters


def run(
    settings: Settings,
    *,
    article_id: str,
    budget: int = 10,
) -> ResearchResult:
    """Verify an article's claims against external sources.

    Args:
        settings:   KEBAB runtime configuration.
        article_id: ID of the article to verify.
        budget:     Maximum number of queries to execute.

    Returns:
        :class:`ResearchResult` summarising the run.
    """
    path = find_article_by_id(settings.CURATED_DIR, article_id)
    if path is None:
        logger.warning("research: article %r not found — skipping", article_id)
        return ResearchResult(article_id=article_id)

    fm, body, tree = read_article(path)

    deps = PlannerDeps(
        settings=settings,
        article_name=fm.name,
        article_body=body,
        available_adapters=_available_adapters(settings),
        budget_hint=budget,
    )
    plan: ResearchPlan = plan_research(settings, deps)
    logger.info(
        "research: %d claims, %d queries for %r",
        len(plan.claims),
        len(plan.queries),
        article_id,
    )

    findings: list[FindingTuple] = []
    confirmed_claims: set[int] = set()
    appended_claims: set[int] = set()
    disputed_claims: set[int] = set()
    finding_summaries: list[str] = []

    queries_run = 0
    for sq in plan.queries:
        if queries_run >= budget:
            logger.info("research: budget of %d queries reached", budget)
            break

        sources = search(settings, sq.adapter, sq.query, limit=2)
        queries_run += 1

        for src in sources:
            for claim_idx in sq.target_claims:
                if claim_idx >= len(plan.claims):
                    continue
                claim: ClaimEntry = plan.claims[claim_idx]

                result: FindingResult = classify_finding(
                    settings, claim, src.title, src.content
                )

                if result.outcome == "dispute":
                    judgment = judge_dispute(settings, claim, result, src.content)
                    if not judgment.is_surfaced:
                        log_event(
                            path, stage="research", action="dispute_suppressed",
                            article_id=article_id,
                            detail=(
                                f"category: {judgment.category} | "
                                f"claim: {claim.text} | "
                                f"reasoning: {judgment.reasoning} | "
                                f"source: {src.title}"
                            ),
                        )
                        continue
                    # Stamp the category on the finding so the writer can show it
                    result = result.model_copy(update={"dispute_category": judgment.category})

                findings.append((claim, result, src.title, src.url))

                if result.outcome == "confirm":
                    confirmed_claims.add(claim_idx)
                    log_event(
                        path, stage="research", action="confirm",
                        article_id=article_id,
                        detail=f"Claim confirmed: {claim.text} (source: {src.title})",
                    )
                elif result.outcome == "append":
                    appended_claims.add(claim_idx)
                    log_event(
                        path, stage="research", action="append",
                        article_id=article_id,
                        detail=f"New info appended: {result.new_sentence or ''} (source: {src.title})",
                    )
                elif result.outcome == "dispute":
                    disputed_claims.add(claim_idx)
                    log_event(
                        path, stage="research", action="dispute",
                        article_id=article_id,
                        detail=(
                            f"category: {result.dispute_category} | "
                            f"claim: {claim.text} | "
                            f"contradiction: {result.contradiction or ''} | "
                            f"reasoning: {result.reasoning} | "
                            f"source: {src.title}"
                        ),
                    )

    # Synthesize multiple appends per section into one cohesive statement.
    # Build a footnote_refs map so the synthesizer knows which [^N] markers
    # to preserve in the merged text.
    if findings:
        footnote_refs: dict[str, str] = {}
        fn_num = next_footnote_number(tree)
        for _, finding, _title, url in findings:
            if finding.outcome == "append" and url not in footnote_refs:
                footnote_refs[url] = f"[^{fn_num}]"
                fn_num += 1
        findings = merge_appends(settings, findings, footnote_refs)

    new_body = apply_findings_to_article(body, findings) if findings else body

    # Parse the mutated body to get an AST for tree-based helpers.
    new_tree = parse_body(new_body)
    setattr(fm, "research_claims_total", len(plan.claims))
    setattr(fm, "external_confirms", count_external_footnotes(new_tree))
    setattr(fm, "dispute_count", extract_disputes(new_tree))
    setattr(fm, "researched_at", date.today().isoformat())

    write_article(path, fm, new_body)
    logger.info(
        "research: wrote %r — confirms=%d/%d appends=%d disputes=%d",
        path.name,
        len(confirmed_claims),
        len(plan.claims),
        len(appended_claims),
        len(disputed_claims),
    )

    # Auto-sync to Qdrant (updates confidence_level)
    from app.agents.sync import auto_sync
    auto_sync(settings, "research")

    return ResearchResult(
        article_id=article_id,
        claims_total=len(plan.claims),
        confirms=len(confirmed_claims),
        appends=len(appended_claims),
        disputes=len(disputed_claims),
        findings=finding_summaries,
    )


__all__ = ["ResearchResult", "run"]
