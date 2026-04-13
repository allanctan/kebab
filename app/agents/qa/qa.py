"""Gap discovery agent — identifies knowledge holes in curated articles.

Reads the article body and discovers questions the article SHOULD answer
but doesn't. These gaps feed into ``research-gaps`` which searches
external sources for answers.

Single LLM call per article. Exhausts all meaningful gaps in one pass.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.config.config import Settings
from app.core.errors import KebabError
from app.core.llm.resolve import resolve_model
from app.core.markdown import (
    extract_research_gaps,
    read_article,
    write_article,
)

logger = logging.getLogger(__name__)

_GAP_PROMPT = Path(__file__).parent / "prompts" / "gap_discover.md"


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------


class GapQuestion(BaseModel):
    """A question relevant to the topic but not answerable from the article."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., description="The gap question.")
    reasoning: str = Field(..., description="Why this gap matters for the topic.")


class GapDiscoveryResult(BaseModel):
    """Output of the gap discovery call."""

    model_config = ConfigDict(extra="forbid")

    gap_questions: list[GapQuestion] = Field(
        default_factory=list,
        description="All meaningful gaps. Empty list is valid.",
    )


# ---------------------------------------------------------------------------
# Deps + run result
# ---------------------------------------------------------------------------


@dataclass
class QaDeps:
    """Runtime context for one agent call."""

    settings: Settings
    article_id: str
    article_name: str
    body: str
    existing_gaps: list[str]
    cited_sources: list[str]
    context_metadata: str


@dataclass
class QaRunResult:
    """Summary of one ``run`` invocation."""

    updated: list[Path]
    gaps_added: int
    skipped: list[tuple[Path, str]]


QaProposer = Callable[[Settings, QaDeps], GapDiscoveryResult]


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def _default_proposer(settings: Settings, deps: QaDeps) -> GapDiscoveryResult:
    """Discover all knowledge gaps in the article."""
    agent: Agent[QaDeps, GapDiscoveryResult] = Agent(
        model=resolve_model(settings.QA_MODEL),
        deps_type=QaDeps,
        output_type=GapDiscoveryResult,
        system_prompt=_GAP_PROMPT.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )
    user = (
        f"article_name: {deps.article_name}\n"
        f"existing_gaps: {deps.existing_gaps}\n"
        f"context_metadata: {deps.context_metadata}\n\n"
        f"body:\n{deps.body}"
    )
    return agent.run_sync(user, deps=deps).output


# ---------------------------------------------------------------------------
# Article processing + run loop
# ---------------------------------------------------------------------------


def _iter_articles(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.md"))


def _process_article(
    settings: Settings,
    path: Path,
    proposer: QaProposer,
) -> tuple[bool, int, str | None]:
    """Run the agent on a single article. Returns ``(updated, n_gaps, error)``."""
    fm, body, tree = read_article(path)
    contexts = fm.model_dump().get("contexts", {})
    context_str = str(contexts) if contexts else "none"
    deps = QaDeps(
        settings=settings,
        article_id=fm.id,
        article_name=fm.name,
        body=body,
        existing_gaps=extract_research_gaps(tree),
        cited_sources=[src.title for src in fm.sources],
        context_metadata=context_str,
    )
    if not deps.cited_sources:
        return (False, 0, "no cited sources — qa requires grounding")
    try:
        result = proposer(settings, deps)
    except Exception as exc:  # noqa: BLE001
        return (False, 0, str(exc))
    if not result.gap_questions:
        return (False, 0, None)

    from app.core.markdown import append_research_gaps

    gap_texts = [gq.question for gq in result.gap_questions]
    new_body = append_research_gaps(body, gap_texts)

    if new_body == body:
        return (False, 0, None)

    write_article(path, fm, new_body)

    from app.core.audit import log_event

    for gq in result.gap_questions:
        log_event(
            path,
            stage="qa",
            action="gap_discovered",
            article_id=fm.id,
            question=gq.question,
            reasoning=gq.reasoning,
        )

    n_gaps = len(result.gap_questions)
    logger.info("qa: %s +%d gap(s)", path, n_gaps)
    return (True, n_gaps, None)


def run(
    settings: Settings,
    *,
    article_id: str | None = None,
    domain: str | None = None,
    once: bool = True,
    watch: bool = False,
    proposer: QaProposer = _default_proposer,
    sleep_seconds: float = 60.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    iterations: int | None = None,
) -> QaRunResult:
    """Run the gap discovery loop.

    - ``article_id`` processes a single article.
    - ``domain`` filters to articles under ``curated/<domain>/``.
    - ``once=True`` (default) runs a single pass and exits.
    - ``watch=True`` loops forever (or until ``iterations`` is exhausted in tests).
    """
    if once and watch:
        raise KebabError("qa: --once and --watch are mutually exclusive")

    if article_id:
        from app.core.markdown import find_article_by_id

        target = find_article_by_id(settings.CURATED_DIR, article_id)
        all_paths = [target] if target else []
    else:
        root = Path(settings.CURATED_DIR) / domain if domain else Path(settings.CURATED_DIR)
        all_paths = _iter_articles(root)

    updated: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    gaps_added = 0
    runs_done = 0

    while True:
        for path in all_paths:
            ok, added, error = _process_article(settings, path, proposer)
            if ok:
                updated.append(path)
                gaps_added += added
            elif error is not None:
                skipped.append((path, error))
        runs_done += 1
        if not watch:
            break
        if iterations is not None and runs_done >= iterations:
            break
        sleep_fn(sleep_seconds)

    return QaRunResult(updated=updated, gaps_added=gaps_added, skipped=skipped)
