"""Generate orchestrator — chains contexts → gaps → write in one step.

Single entry point: ``run(settings)`` classifies contexts, finds gaps,
and generates articles (with summary included in each article).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.config import Settings
from app.pipeline.generate.contexts import run as run_contexts
from app.pipeline.generate.gaps import run as run_gaps
from app.pipeline.generate.writer import write_articles

logger = logging.getLogger(__name__)


@dataclass
class GenerateStageResult:
    """Combined result of the full generate stage."""

    contexts_updated: int = 0
    gaps_found: int = 0
    articles_written: int = 0
    articles_skipped: int = 0


def run(settings: Settings, *, domain: str = "default", force: bool = False, **kwargs: Any) -> GenerateStageResult:
    """Execute the full generate stage: contexts → gaps → write.

    ``domain`` selects which plan to use (e.g. "science", "legal").
    ``force`` regenerates all articles in the plan and re-runs contexts.
    """
    result = GenerateStageResult()

    # Collect article paths for this domain from the plan
    from app.pipeline.organize import load_plan
    plan = load_plan(settings, domain)
    domain_paths: list[Path] = []
    if plan:
        for node in plan.nodes:
            if node.level_type == "article" and node.md_path:
                p = Path(node.md_path)
                if p.exists():
                    domain_paths.append(p)

    # Step 1: Classify contexts (before writing so writer can use them)
    # Map domain to vertical — domain name matches vertical key (case-insensitive)
    from app.pipeline.generate.contexts import VERTICALS
    domain_lower = domain.lower()
    context_cls = VERTICALS.get(domain_lower)
    ctx_result = run_contexts(settings, article_paths=domain_paths or None, context_cls=context_cls)
    result.contexts_updated = len(ctx_result.updated)
    logger.info("generate: %d context(s) updated", result.contexts_updated)

    # Step 2: Find gaps
    if force:
        from app.pipeline.generate.gaps import Gap, GapReport
        if plan is None:
            logger.warning("generate: no plan for domain %r", domain)
            return result
        forced_gaps = [
            Gap(
                id=n.id, name=n.name, description=n.description,
                source_files=list(n.source_files),
                target_path=n.md_path, reason="new",
            )
            for n in plan.nodes if n.level_type == "article"
        ]
        gap_report = GapReport(gaps=forced_gaps, existing=[])
        result.gaps_found = len(forced_gaps)
        logger.info("generate: force mode — %d article(s) to regenerate", result.gaps_found)
    else:
        gap_result = run_gaps(settings, domain=domain)
        gap_report = gap_result.report
        result.gaps_found = len(gap_report.gaps)
        logger.info("generate: %d gap(s) found", result.gaps_found)

    # Step 3: Write articles (includes summary in each article)
    if gap_report.gaps:
        write_result = write_articles(settings, domain=domain, gaps=gap_report, **kwargs)
        result.articles_written = len(write_result.written)
        result.articles_skipped = len(write_result.skipped)
        logger.info(
            "generate: %d written, %d skipped",
            result.articles_written, result.articles_skipped,
        )

    return result
