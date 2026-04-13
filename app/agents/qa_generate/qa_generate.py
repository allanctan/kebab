"""Q&A generation agent — creates grade-appropriate question-answer pairs.

Phase 3 post-processing step. Reads verified, complete article content
and generates grounded Q&A pairs targeted at the article's grade level.
Separate from gap discovery (Phase 2) — this runs after the content
is authoritative and complete.

Single LLM call per article. Exhausts all meaningful questions in one pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.config.config import Settings
from app.core.audit import log_event
from app.core.llm.resolve import resolve_model
from app.core.markdown import (
    extract_faq,
    insert_section_ordered,
    read_article,
    write_article,
)
from app.models.source import Source

logger = logging.getLogger(__name__)

_PROMPT = Path(__file__).parent / "prompts" / "qa_generate.md"


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


@dataclass
class QaGenerateDeps:
    settings: Settings
    article_name: str
    body: str
    existing_questions: list[str]
    context_metadata: str


@dataclass
class QaGenerateRunResult:
    """Summary of one run."""

    updated: list[Path]
    pairs_added: int
    skipped: list[tuple[Path, str]]


def _generate(settings: Settings, deps: QaGenerateDeps) -> list[QaPair]:
    """Generate all grounded Q&A pairs for an article."""
    agent: Agent[QaGenerateDeps, QaGenerateResult] = Agent(
        model=resolve_model(settings.QA_MODEL),
        deps_type=QaGenerateDeps,
        output_type=QaGenerateResult,
        system_prompt=_PROMPT.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )
    user = (
        f"article_name: {deps.article_name}\n"
        f"existing_questions: {deps.existing_questions}\n"
        f"context_metadata: {deps.context_metadata}\n\n"
        f"body:\n{deps.body}"
    )
    return agent.run_sync(user, deps=deps).output.new_questions


def _append_pairs(body: str, pairs: list[QaPair]) -> str:
    """Append Q&A pairs at the correct position in the article."""
    new_block = "\n".join(
        f"**Q: {pair.question}**\n{pair.answer}\n" for pair in pairs
    )
    return insert_section_ordered(body, "Q&A", new_block)


def run(
    settings: Settings,
    *,
    article_id: str | None = None,
    domain: str | None = None,
) -> QaGenerateRunResult:
    """Generate Q&A pairs for articles.

    - ``article_id`` processes a single article.
    - ``domain`` filters to articles under ``curated/<domain>/``.
    - Omit both to process all articles.
    """
    from app.core.markdown import find_article_by_id

    if article_id:
        target = find_article_by_id(settings.CURATED_DIR, article_id)
        paths = [target] if target else []
    else:
        root = Path(settings.CURATED_DIR) / domain if domain else Path(settings.CURATED_DIR)
        paths = sorted(root.rglob("*.md")) if root.exists() else []

    updated: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    pairs_added = 0

    for path in paths:
        try:
            fm, body, tree = read_article(path)
        except Exception as exc:  # noqa: BLE001
            skipped.append((path, str(exc)))
            continue

        if not fm.sources:
            skipped.append((path, "no sources"))
            continue

        contexts = fm.model_dump().get("contexts", {})
        context_str = str(contexts) if contexts else "none"
        deps = QaGenerateDeps(
            settings=settings,
            article_name=fm.name,
            body=body,
            existing_questions=extract_faq(tree),
            context_metadata=context_str,
        )

        try:
            new_pairs = _generate(settings, deps)
        except Exception as exc:  # noqa: BLE001
            skipped.append((path, str(exc)))
            continue

        if not new_pairs:
            continue

        # Drop exact duplicates
        existing = set(deps.existing_questions)
        fresh = [p for p in new_pairs if p.question not in existing]
        if not fresh:
            continue

        new_body = _append_pairs(body, fresh)
        write_article(path, fm, new_body)
        updated.append(path)
        pairs_added += len(fresh)

        for pair in fresh:
            log_event(
                path, stage="qa-generate", action="qa_added",
                article_id=fm.id,
                question=pair.question,
                answer=pair.answer,
            )

        logger.info("qa-generate: %s +%d pair(s)", path.name, len(fresh))

    return QaGenerateRunResult(updated=updated, pairs_added=pairs_added, skipped=skipped)
