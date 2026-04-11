"""Q&A enrichment agent.

Continuously walks curated articles, asks bridging/deepening questions,
and appends grounded answers to the ``## Q&A`` section of each markdown
file. Pattern mirrors
``better-ed-ai/app/agents/assignment/assignment_checker.py``: dataclass
deps, ``Agent(model=..., deps_type=..., output_type=...)``, prompt loaded
from a markdown file.
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
from app.core.markdown import extract_faq, read_article, write_article
from app.models.source import Source

logger = logging.getLogger(__name__)


_PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"


class QaPair(BaseModel):
    """One grounded question/answer pair."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., description="Atomic question.")
    answer: str = Field(..., description="1–3 sentence grounded answer.")
    sources: list[Source] = Field(
        ..., min_length=1, description="At least one cited source — enforced."
    )


class GapQuestion(BaseModel):
    """A question relevant to the topic but not answerable from the article."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., description="The gap question.")
    reasoning: str = Field(..., description="Why this gap matters for the topic.")


class QaResult(BaseModel):
    """Output of one agent call."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str = Field(..., description="Brief analysis of the gaps the pairs fill.")
    new_questions: list[QaPair] = Field(
        default_factory=list, description="At most 5 new pairs."
    )
    gap_questions: list[GapQuestion] = Field(
        default_factory=list, description="Up to 5 questions the article doesn't answer."
    )
    is_ready_to_commit: bool = Field(
        ..., description="True once at least one new grounded pair or gap question is produced."
    )


@dataclass
class QaDeps:
    """Runtime context for one agent call."""

    settings: Settings
    article_id: str
    article_name: str
    body: str
    existing_questions: list[str]
    cited_sources: list[str]
    context_metadata: str


@dataclass
class QaRunResult:
    """Summary of one ``run`` invocation."""

    updated: list[Path]
    pairs_added: int
    skipped: list[tuple[Path, str]]


QaProposer = Callable[[Settings, QaDeps], QaResult]


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def build_qa_agent(settings: Settings) -> Agent[QaDeps, QaResult]:
    return Agent(
        model=resolve_model(settings.QA_MODEL),
        deps_type=QaDeps,
        output_type=QaResult,
        system_prompt=_load_prompt(),
        retries=settings.LLM_MAX_RETRIES,
    )


def _default_proposer(settings: Settings, deps: QaDeps) -> QaResult:
    agent = build_qa_agent(settings)
    user = (
        f"article_id: {deps.article_id}\n"
        f"article_name: {deps.article_name}\n"
        f"existing_questions: {deps.existing_questions}\n"
        f"cited_sources: {deps.cited_sources}\n"
        f"context_metadata: {deps.context_metadata}\n\n"
        f"body:\n{deps.body}"
    )
    return agent.run_sync(user, deps=deps).output


def _iter_articles(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.md"))


def _has_qa_section(body: str) -> bool:
    for line in body.splitlines():
        if line.strip().lower().startswith("## q&a"):
            return True
    return False


def _append_pairs(body: str, pairs: list[QaPair]) -> str:
    """Append new ``**Q:`` blocks to the existing ``## Q&A`` section, or create one."""
    new_block = "\n".join(
        f"\n**Q: {pair.question}**\n{pair.answer}\n" for pair in pairs
    )
    if _has_qa_section(body):
        return body.rstrip() + "\n" + new_block
    return body.rstrip() + "\n\n## Q&A\n" + new_block


def _process_article(
    settings: Settings,
    path: Path,
    proposer: QaProposer,
    today: datetime,
) -> tuple[bool, int, str | None]:
    """Run the agent on a single article. Returns ``(updated, n_added, error)``."""
    fm, body = read_article(path)
    contexts = fm.model_dump().get("contexts", {})
    context_str = str(contexts) if contexts else "none"
    deps = QaDeps(
        settings=settings,
        article_id=fm.id,
        article_name=fm.name,
        body=body,
        existing_questions=extract_faq(body),
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
    # Drop duplicates the agent missed.
    existing = set(deps.existing_questions)
    fresh = [pair for pair in result.new_questions if pair.question not in existing]
    if fresh:
        new_body = _append_pairs(body, fresh)
    else:
        new_body = body

    if result.gap_questions:
        from app.core.markdown import append_research_gaps
        gap_texts = [gq.question for gq in result.gap_questions]
        new_body = append_research_gaps(new_body, gap_texts)

    if new_body == body:
        return (False, 0, None)

    write_article(path, fm, new_body)
    logger.info("qa: %s +%d pair(s) (today=%s)", path, len(fresh), today.date())
    return (True, len(fresh), None)


def run(
    settings: Settings,
    *,
    once: bool = True,
    watch: bool = False,
    proposer: QaProposer = _default_proposer,
    sleep_seconds: float = 60.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    iterations: int | None = None,
) -> QaRunResult:
    """Run the Q&A enrichment loop.

    - ``once=True`` (default) runs a single pass and exits.
    - ``watch=True`` loops forever (or until ``iterations`` is exhausted in tests).
    """
    if once and watch:
        raise KebabError("qa: --once and --watch are mutually exclusive")

    updated: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    pairs_added = 0
    runs_done = 0

    while True:
        for path in _iter_articles(Path(settings.CURATED_DIR)):
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
