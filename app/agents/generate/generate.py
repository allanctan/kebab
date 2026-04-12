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
from app.agents.generate.contexts import run as run_contexts
from app.agents.generate.gaps import run as run_gaps
from app.agents.generate.writer import write_articles

logger = logging.getLogger(__name__)


@dataclass
class GenerateStageResult:
    """Combined result of the full generate stage."""

    contexts_updated: int = 0
    gaps_found: int = 0
    articles_written: int = 0
    articles_skipped: int = 0


def run(
    settings: Settings,
    *,
    domain: str = "default",
    article_id: str | None = None,
    force: bool = False,
    **kwargs: Any,
) -> GenerateStageResult:
    """Execute the full generate stage: gaps → write → contexts.

    ``domain`` selects which plan to use (e.g. "science", "legal").
    ``article_id`` generates a single article (requires ``--force``).
    ``force`` regenerates articles even if already written.
    """
    result = GenerateStageResult()

    # Load the plan first — bail early if the domain doesn't exist,
    # before spending LLM calls on contexts classification.
    from app.agents.organize import load_plan
    plan = load_plan(settings, domain)
    if plan is None:
        logger.warning("generate: no plan for domain %r — run `kebab organize --domain %s` first", domain, domain)
        return result

    domain_paths: list[Path] = []
    for node in plan.nodes:
        if node.level_type == "article" and node.md_path:
            p = Path(node.md_path)
            if p.exists():
                domain_paths.append(p)

    if not domain_paths:
        logger.warning("generate: plan for %r has no existing article stubs — run `kebab organize --domain %s` first", domain, domain)
        return result

    # Step 1: Find gaps
    if force:
        from app.agents.generate.gaps import Gap, GapReport
        target_nodes = [
            n for n in plan.nodes
            if n.level_type == "article"
            and (article_id is None or n.id == article_id)
        ]
        if article_id and not target_nodes:
            logger.warning("generate: article %r not found in plan for domain %r", article_id, domain)
            return result
        forced_gaps = [
            Gap(
                id=n.id, name=n.name, description=n.description,
                source_files=list(n.source_files),
                target_path=n.md_path, reason="new",
            )
            for n in target_nodes
        ]
        gap_report = GapReport(gaps=forced_gaps, existing=[])
        result.gaps_found = len(forced_gaps)
        logger.info("generate: force mode — %d article(s) to regenerate", result.gaps_found)
    else:
        gap_result = run_gaps(settings, domain=domain)
        gap_report = gap_result.report
        result.gaps_found = len(gap_report.gaps)
        logger.info("generate: %d gap(s) found", result.gaps_found)

    # Step 2: Write articles (includes summary in each article).
    # The writer reads BASE_INSTRUCTION from any pre-existing context on
    # disk (from a prior run). On a fresh run there's no context yet, so
    # the writer falls back to a generic instruction — that's fine; the
    # body still gets written and contexts classifies it in step 3.
    if gap_report.gaps:
        write_result = write_articles(settings, domain=domain, gaps=gap_report, **kwargs)
        result.articles_written = len(write_result.written)
        result.articles_skipped = len(write_result.skipped)
        logger.info(
            "generate: %d written, %d skipped",
            result.articles_written, result.articles_skipped,
        )

    # Step 3: Classify contexts AFTER writing — the classifier reads the
    # article body to determine vertical (education, healthcare, legal,
    # policy) and populate fields like grade, subject, bloom_level, etc.
    # On a fresh run this is the first time the articles have real body
    # content. Re-read domain_paths to include newly-written articles.
    written_paths: list[Path] = []
    for node in plan.nodes:
        if node.level_type == "article" and node.md_path:
            if article_id is not None and node.id != article_id:
                continue
            p = Path(node.md_path)
            if p.exists():
                written_paths.append(p)

    ctx_result = run_contexts(settings, article_paths=written_paths)
    result.contexts_updated = len(ctx_result.updated)
    logger.info("generate: %d context(s) updated", result.contexts_updated)

    # Step 4: Auto-sync to Qdrant
    from app.agents.sync import auto_sync
    auto_sync(settings, "generate")

    return result
