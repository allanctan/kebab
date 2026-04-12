"""Research-gaps agent — answer unanswered questions in an article.

Reads the ``## Research Gaps`` section of a curated article, plans
search queries for each unanswered question, runs them via the shared
:mod:`app.core.research.searcher`, classifies each source against the
question, and rewrites the gaps section in-place with Q/A blocks.

Independent of :mod:`app.agents.research`. The supervisor agent (future)
can call this directly without first running claim verification.
"""

from __future__ import annotations

import logging
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.agents.research_gaps.classifier import answer_question
from app.agents.research_gaps.query_planner import (
    GapQueryPlan,
    QueryPlannerDeps,
    plan_queries,
)
from app.agents.research_gaps.writer import GapAnswer, apply_answers_to_gaps
from app.config.config import Settings
from app.core.audit import log_event
from app.core.markdown import (
    extract_research_gaps,
    find_article_by_id,
    read_article,
    write_article,
)
from app.core.research.searcher import search

logger = logging.getLogger(__name__)


class GapsResult(BaseModel):
    """Summary of one research-gaps run."""

    model_config = ConfigDict(extra="forbid")

    article_id: str = Field(..., description="ID of the article processed.")
    gaps_total: int = Field(default=0, description="Number of unanswered gaps found.")
    answered: int = Field(default=0, description="Number of gaps answered this run.")
    findings: list[str] = Field(
        default_factory=list,
        description="Human-readable summary of each answered gap.",
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
    budget: int = 5,
) -> GapsResult:
    """Answer unanswered gaps in an article.

    Args:
        settings:   KEBAB runtime configuration.
        article_id: ID of the article whose gaps should be answered.
        budget:     Maximum number of queries to execute.

    Returns:
        :class:`GapsResult` summarising the run.
    """
    path = find_article_by_id(settings.CURATED_DIR, article_id)
    if path is None:
        logger.warning("research-gaps: article %r not found — skipping", article_id)
        return GapsResult(article_id=article_id)

    fm, body, tree = read_article(path)
    all_gaps = extract_research_gaps(tree)
    gaps = [g for g in all_gaps if not g.startswith("**Q:")]
    if not gaps:
        logger.info("research-gaps: no unanswered gaps for %r — skipping", article_id)
        return GapsResult(article_id=article_id)

    deps = QueryPlannerDeps(
        settings=settings,
        article_name=fm.name,
        gap_questions=gaps,
        available_adapters=_available_adapters(settings),
        budget_hint=budget,
    )
    plan: GapQueryPlan = plan_queries(settings, deps)
    logger.info(
        "research-gaps: %d unanswered gaps, %d queries for %r",
        len(gaps),
        len(plan.queries),
        article_id,
    )

    answers: list[GapAnswer] = []
    finding_summaries: list[str] = []
    answered_idx: set[int] = set()
    queries_run = 0

    for gq in plan.queries:
        if queries_run >= budget:
            logger.info("research-gaps: budget of %d queries reached", budget)
            break
        if gq.target_gap_idx in answered_idx:
            continue
        if gq.target_gap_idx < 0 or gq.target_gap_idx >= len(gaps):
            continue

        sources = search(settings, gq.adapter, gq.query, limit=2)
        queries_run += 1

        for src in sources:
            classification = answer_question(
                settings,
                question=gaps[gq.target_gap_idx],
                source_title=src.title,
                source_content=src.content,
            )
            if not classification.is_answered:
                continue
            answer = GapAnswer(
                gap_idx=gq.target_gap_idx,
                answer_text=classification.answer,
                source_title=src.title,
                source_url=src.url,
            )
            answers.append(answer)
            answered_idx.add(gq.target_gap_idx)
            summary = f"answered gap {gq.target_gap_idx}: {gaps[gq.target_gap_idx][:60]!r} via {src.title!r}"
            finding_summaries.append(summary)
            log_event(
                path, stage="research-gaps", action="gap_answered",
                article_id=article_id,
                question=gaps[gq.target_gap_idx],
                answer=classification.answer,
                source_title=src.title, source_url=src.url,
            )
            break

    new_body = apply_answers_to_gaps(body, gaps, answers) if answers else body

    setattr(fm, "gaps_answered", len(answers))
    setattr(fm, "gaps_researched_at", date.today().isoformat())

    write_article(path, fm, new_body)
    logger.info(
        "research-gaps: wrote %r — answered=%d/%d",
        path.name,
        len(answers),
        len(gaps),
    )

    return GapsResult(
        article_id=article_id,
        gaps_total=len(gaps),
        answered=len(answers),
        findings=finding_summaries,
    )


__all__ = ["GapsResult", "run"]
