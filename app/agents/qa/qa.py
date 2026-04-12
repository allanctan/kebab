"""Q&A enrichment agent — two-pass: generate Q&A pairs then discover gaps.

Pass 1: LLM reads the article body and generates ALL grounded Q&A pairs
it can support. No cap — exhausts meaningful questions in one call.

Pass 2: LLM identifies knowledge gaps — questions the article SHOULD
answer but doesn't. These feed into ``research-gaps`` for external
source answering.

Two separate LLM calls with separate prompts, so each can focus on
its task without context-switching between grounding and reasoning
about what's missing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.config.config import Settings
from app.core.errors import KebabError
from app.core.llm.resolve import resolve_model
from app.core.markdown import (
    extract_faq,
    extract_research_gaps,
    read_article,
    write_article,
)
from app.models.source import Source

logger = logging.getLogger(__name__)

_QA_PROMPT = Path(__file__).parent / "prompts" / "qa_generate.md"
_GAP_PROMPT = Path(__file__).parent / "prompts" / "gap_discover.md"


# ---------------------------------------------------------------------------
# Output models — one per LLM call
# ---------------------------------------------------------------------------


class QaPair(BaseModel):
    """One grounded question/answer pair."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., description="Atomic question.")
    answer: str = Field(..., description="1–3 sentence grounded answer.")
    sources: list[Source] = Field(
        ..., min_length=1, description="At least one cited source — enforced."
    )


class QaGenerateResult(BaseModel):
    """Output of the Q&A generation call."""

    model_config = ConfigDict(extra="forbid")

    new_questions: list[QaPair] = Field(
        default_factory=list,
        description="All grounded Q&A pairs the article supports. "
        "Empty list is valid if article is well-covered.",
    )


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


# Legacy compat — tests reference QaResult
class QaResult(BaseModel):
    """Combined result from both passes."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str = Field(default="", description="Not used — kept for compat.")
    new_questions: list[QaPair] = Field(default_factory=list)
    gap_questions: list[GapQuestion] = Field(default_factory=list)
    is_ready_to_commit: bool = Field(default=False)


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
    existing_questions: list[str]
    existing_gaps: list[str]
    cited_sources: list[str]
    context_metadata: str


@dataclass
class QaRunResult:
    """Summary of one ``run`` invocation."""

    updated: list[Path]
    pairs_added: int
    skipped: list[tuple[Path, str]]


QaProposer = Callable[[Settings, QaDeps], QaResult]


# ---------------------------------------------------------------------------
# Two-pass LLM calls
# ---------------------------------------------------------------------------


def _generate_qa(settings: Settings, deps: QaDeps) -> list[QaPair]:
    """Pass 1: generate all grounded Q&A pairs."""
    agent: Agent[QaDeps, QaGenerateResult] = Agent(
        model=resolve_model(settings.QA_MODEL),
        deps_type=QaDeps,
        output_type=QaGenerateResult,
        system_prompt=_QA_PROMPT.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )
    user = (
        f"article_name: {deps.article_name}\n"
        f"existing_questions: {deps.existing_questions}\n"
        f"context_metadata: {deps.context_metadata}\n\n"
        f"body:\n{deps.body}"
    )
    result = agent.run_sync(user, deps=deps).output
    return result.new_questions


def _discover_gaps(settings: Settings, deps: QaDeps) -> list[GapQuestion]:
    """Pass 2: identify all knowledge gaps."""
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
    result = agent.run_sync(user, deps=deps).output
    return result.gap_questions


def _default_proposer(settings: Settings, deps: QaDeps) -> QaResult:
    """Run both passes and combine into a QaResult."""
    new_questions = _generate_qa(settings, deps)
    gap_questions = _discover_gaps(settings, deps)
    has_content = bool(new_questions or gap_questions)
    return QaResult(
        new_questions=new_questions,
        gap_questions=gap_questions,
        is_ready_to_commit=has_content,
    )


# ---------------------------------------------------------------------------
# Article processing + run loop
# ---------------------------------------------------------------------------


def _iter_articles(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.md"))


def _append_pairs(body: str, pairs: list[QaPair]) -> str:
    """Append new ``**Q:`` blocks to ``## Q&A`` at the correct position."""
    from app.core.markdown import insert_section_ordered

    new_block = "\n".join(
        f"**Q: {pair.question}**\n{pair.answer}\n" for pair in pairs
    )
    return insert_section_ordered(body, "Q&A", new_block)


def _process_article(
    settings: Settings,
    path: Path,
    proposer: QaProposer,
    today: datetime,
) -> tuple[bool, int, str | None]:
    """Run the agent on a single article. Returns ``(updated, n_added, error)``."""
    fm, body, tree = read_article(path)
    contexts = fm.model_dump().get("contexts", {})
    context_str = str(contexts) if contexts else "none"
    deps = QaDeps(
        settings=settings,
        article_id=fm.id,
        article_name=fm.name,
        body=body,
        existing_questions=extract_faq(tree),
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
    if not result.is_ready_to_commit or (not result.new_questions and not result.gap_questions):
        return (False, 0, None)

    # Drop exact duplicates the agent missed.
    existing = set(deps.existing_questions)
    fresh = [pair for pair in result.new_questions if pair.question not in existing]
    new_body = _append_pairs(body, fresh) if fresh else body

    if result.gap_questions:
        from app.core.markdown import append_research_gaps

        gap_texts = [gq.question for gq in result.gap_questions]
        new_body = append_research_gaps(new_body, gap_texts)

    if new_body == body:
        return (False, 0, None)

    write_article(path, fm, new_body)

    from app.core.audit import log_event

    for pair in fresh:
        log_event(
            path,
            stage="qa",
            action="qa_added",
            article_id=fm.id,
            question=pair.question,
            answer=pair.answer,
        )
    if result.gap_questions:
        for gq in result.gap_questions:
            log_event(
                path,
                stage="qa",
                action="gap_discovered",
                article_id=fm.id,
                question=gq.question,
                reasoning=gq.reasoning,
            )

    logger.info("qa: %s +%d pair(s) (today=%s)", path, len(fresh), today.date())
    return (True, len(fresh), None)


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
    """Run the Q&A enrichment loop.

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
    pairs_added = 0
    runs_done = 0

    while True:
        for path in all_paths:
            ok, added, error = _process_article(settings, path, proposer, datetime.now())
            if ok:
                updated.append(path)
                pairs_added += added
            elif error is not None:
                skipped.append((path, error))
        runs_done += 1
        if not watch:
            break
        if iterations is not None and runs_done >= iterations:
            break
        sleep_fn(sleep_seconds)

    return QaRunResult(updated=updated, pairs_added=pairs_added, skipped=skipped)
