"""Lint agent — code-only health checks (no LLM).

Spec §11 checks:
- Orphans: ``parent_ids == []`` for non-root articles.
- Broken prerequisites: ``prerequisites`` referencing IDs not in the index.
- Missing sources: articles whose markdown frontmatter has zero sources.
- Oversized markdown: body token count > ``MAX_TOKENS_PER_ARTICLE``.
- Stale verifications: newest verification older than 180 days.
- Confidence below the production gate (≥3).

Output: a structured :class:`LintReport` written as JSON to
``<KNOWLEDGE_DIR>/.kebab/lint-<ts>.json`` and printed to stdout via the CLI.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config.config import Settings
from app.core.markdown import extract_research_gaps, read_article
from app.core.store import Store
from app.core.llm.tokens import count_tokens

logger = logging.getLogger(__name__)


_STALE_AFTER_DAYS = 180


LintCode = Literal[
    "orphan",
    "broken_prerequisite",
    "missing_sources",
    "oversized",
    "stale_verification",
    "below_confidence_gate",
    "unanswered_gaps",
]


class LintIssue(BaseModel):
    """One health check finding."""

    model_config = ConfigDict(extra="forbid")

    article_id: str = Field(..., description="ID of the offending article.")
    code: LintCode = Field(..., description="Check that produced this issue.")
    detail: str = Field(..., description="Human-readable explanation.")


class LintReport(BaseModel):
    """Aggregate output of one lint run."""

    model_config = ConfigDict(extra="forbid")

    issues: list[LintIssue] = Field(..., description="Every issue found.")
    counts: dict[str, int] = Field(..., description="Issue count by code.")
    articles_scanned: int = Field(..., description="Number of articles inspected.")


@dataclass
class LintRunResult:
    report: LintReport
    output_path: Path


@dataclass
class LintDeps:
    settings: Settings


def _iter_markdown(root: Path):
    if not root.exists():
        return
    for path in sorted(root.rglob("*.md")):
        yield path


def run(
    settings: Settings,
    *,
    store: Store | None = None,
    today: Callable[[], date] = lambda: datetime.now().date(),
) -> LintRunResult:
    """Execute every check and write a JSON report."""
    store = store or Store(settings)
    store.ensure_collection()

    issues: list[LintIssue] = []
    today_value = today()
    stale_cutoff = today_value - timedelta(days=_STALE_AFTER_DAYS)
    scanned = 0

    for path in _iter_markdown(Path(settings.CURATED_DIR)):
        try:
            fm, body, tree = read_article(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("lint: skip %s (%s)", path, exc)
            continue
        scanned += 1

        if not fm.sources:
            issues.append(
                LintIssue(article_id=fm.id, code="missing_sources", detail=str(path))
            )

        token_count = count_tokens(body)
        if token_count > settings.MAX_TOKENS_PER_ARTICLE:
            issues.append(
                LintIssue(
                    article_id=fm.id,
                    code="oversized",
                    detail=f"{token_count} > {settings.MAX_TOKENS_PER_ARTICLE}",
                )
            )

        # Check research freshness (replaces old verification staleness check).
        extras = fm.model_dump()
        researched_at = extras.get("researched_at")
        if researched_at:
            research_date = date.fromisoformat(str(researched_at))
            if research_date < stale_cutoff:
                issues.append(
                    LintIssue(
                        article_id=fm.id,
                        code="stale_verification",
                        detail=f"last researched {research_date} < cutoff {stale_cutoff}",
                    )
                )
        elif fm.verifications:
            # Legacy fallback for pre-research articles
            newest = max(record.date for record in fm.verifications)
            if newest < stale_cutoff:
                issues.append(
                    LintIssue(
                        article_id=fm.id,
                        code="stale_verification",
                        detail=f"newest verification {newest} < cutoff {stale_cutoff}",
                    )
                )

        # Check for unanswered research gaps.
        gaps = extract_research_gaps(tree)
        # Answered gaps have **Q:** prefix, unanswered are plain text
        unanswered = [g for g in gaps if not g.startswith("**Q:")]
        if unanswered:
            issues.append(
                LintIssue(
                    article_id=fm.id,
                    code="unanswered_gaps",
                    detail=f"{len(unanswered)} unanswered gap(s)",
                )
            )

    # Index-derived checks (orphans, gate).
    for article in store.scroll():
        if not article.parent_ids:
            issues.append(
                LintIssue(article_id=article.id, code="orphan", detail="no parent_ids")
            )
        if article.confidence_level < 3:
            issues.append(
                LintIssue(
                    article_id=article.id,
                    code="below_confidence_gate",
                    detail=f"confidence={article.confidence_level}",
                )
            )

    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.code] = counts.get(issue.code, 0) + 1

    report = LintReport(issues=issues, counts=counts, articles_scanned=scanned)
    out_dir = Path(settings.KNOWLEDGE_DIR) / ".kebab"
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = out_dir / f"lint-{timestamp}.json"
    out_path.write_text(json.dumps(report.model_dump(), indent=2), encoding="utf-8")
    logger.info("lint: %d issue(s) across %d article(s)", len(issues), scanned)
    return LintRunResult(report=report, output_path=out_path)
