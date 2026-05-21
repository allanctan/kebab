"""LLM-driven competency tagger for existing curated articles.

For each article, narrow the candidate LC list by the article's subject
(when available in ``contexts.education.subject``), prompt the LLM to
select which LCs the article materially covers, and write the result
into the article's frontmatter (``competency_codes``, ``curriculum``).

Idempotent: re-running on an already-tagged article overwrites the
codes with the fresh judgment. Use this for backfill **and** as a
maintenance pass when the spine refreshes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.agents.curriculum.curriculum import load_spine
from app.config.config import Settings
from app.core.llm.resolve import resolve_model
from app.core.markdown import read_article, write_article
from app.models.curriculum import Competency

logger = logging.getLogger(__name__)

_TAG_PROMPT = Path(__file__).parent / "prompts" / "tag.md"


# ---------------------------------------------------------------------------
# LLM I/O models
# ---------------------------------------------------------------------------


class TagResult(BaseModel):
    """Output of the tagging call."""

    model_config = ConfigDict(extra="forbid")

    competency_codes: list[str] = Field(
        default_factory=list,
        description="LC codes the article materially covers. Empty is valid.",
    )
    reasoning: str = Field(
        default="",
        description="Short justification for selection (debug only).",
    )


# ---------------------------------------------------------------------------
# Run-time deps + result
# ---------------------------------------------------------------------------


@dataclass
class TagDeps:
    """Runtime context for one tagging call."""

    settings: Settings
    article_id: str
    article_name: str
    article_summary: str
    body: str
    candidate_competencies: list[Competency]
    curriculum_name: str


@dataclass
class ArticleTagOutcome:
    """Result of tagging a single article."""

    path: Path
    article_id: str
    previous_codes: list[str]
    new_codes: list[str]
    skipped_reason: str | None = None


TagProposer = Callable[[Settings, TagDeps], TagResult]


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def _default_proposer(settings: Settings, deps: TagDeps) -> TagResult:
    """Ask the LLM which candidate LCs the article materially covers."""
    agent: Agent[TagDeps, TagResult] = Agent(
        model=resolve_model(settings.CONTEXTS_MODEL),
        deps_type=TagDeps,
        output_type=TagResult,
        system_prompt=_TAG_PROMPT.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )
    candidates_block = "\n".join(
        f"- code: {c.code}\n  domain: {c.domain or '(none)'}\n"
        f"  subdomain: {c.subdomain or '(none)'}\n  competency: {c.competency}"
        for c in deps.candidate_competencies
    )
    parts = [
        f"article_name: {deps.article_name}",
        f"article_summary: {deps.article_summary or '(none provided)'}",
        "",
        "candidate_competencies:",
        candidates_block or "(no candidates — return empty list)",
        "",
        "body:",
        deps.body,
    ]
    return agent.run_sync("\n".join(parts), deps=deps).output


# ---------------------------------------------------------------------------
# Subject filter
# ---------------------------------------------------------------------------


def _article_subject(fm_data: dict) -> str | None:
    """Pull the education subject out of an article's contexts, if present."""
    contexts = fm_data.get("contexts") or {}
    if not isinstance(contexts, dict):
        return None
    education = contexts.get("education") or {}
    if not isinstance(education, dict):
        return None
    subject = education.get("subject")
    return str(subject).strip().lower() if subject else None


def _filter_candidates(
    competencies: list[Competency],
    subject: str | None,
) -> list[Competency]:
    """Narrow the LC list to those matching the article's subject.

    Matching is case-insensitive. If the article has no recorded
    subject, return *all* competencies — the LLM still does the
    filtering on its end, just at higher cost.
    """
    if not subject:
        return competencies
    return [c for c in competencies if c.subject.lower() == subject]


# ---------------------------------------------------------------------------
# Per-article processing
# ---------------------------------------------------------------------------


def _tag_one(
    settings: Settings,
    path: Path,
    competencies: list[Competency],
    curriculum_name: str,
    proposer: TagProposer,
) -> ArticleTagOutcome:
    fm, body, _ = read_article(path)
    fm_data = fm.model_dump()
    previous_codes = list(fm_data.get("competency_codes") or [])
    subject = _article_subject(fm_data)
    candidates = _filter_candidates(competencies, subject)

    if not candidates:
        return ArticleTagOutcome(
            path=path,
            article_id=fm.id,
            previous_codes=previous_codes,
            new_codes=previous_codes,
            skipped_reason=f"no candidates for subject={subject!r}",
        )

    deps = TagDeps(
        settings=settings,
        article_id=fm.id,
        article_name=fm.name,
        article_summary=str(fm_data.get("summary") or ""),
        body=body,
        candidate_competencies=candidates,
        curriculum_name=curriculum_name,
    )
    result = proposer(settings, deps)

    # Validate: returned codes must exist in the candidate set
    candidate_codes = {c.code for c in candidates}
    new_codes = sorted({c for c in result.competency_codes if c in candidate_codes})

    setattr(fm, "competency_codes", new_codes)
    setattr(fm, "curriculum", curriculum_name)
    write_article(path, fm, body)
    logger.info(
        "curriculum tag: %s — %d → %d codes",
        path.name,
        len(previous_codes),
        len(new_codes),
    )
    return ArticleTagOutcome(
        path=path,
        article_id=fm.id,
        previous_codes=previous_codes,
        new_codes=new_codes,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def tag_articles(
    settings: Settings,
    *,
    name: str,
    domain: str | None = None,
    article_id: str | None = None,
    proposer: TagProposer = _default_proposer,
) -> list[ArticleTagOutcome]:
    """Tag curated articles against the named curriculum spine.

    Args:
        settings:   KEBAB runtime configuration.
        name:       Spine name (matches a file under .kebab/curriculum/).
        domain:     If set, only tag articles under ``curated/<domain>/``.
        article_id: If set, only tag the article with this ID.
        proposer:   Override for the LLM call (used in tests).

    Returns:
        Per-article outcomes including the previous and new code lists.

    Raises:
        FileNotFoundError: if the spine doesn't exist.
    """
    spine = load_spine(settings, name)
    competencies = spine.competencies

    if article_id is not None:
        from app.core.markdown import find_article_by_id

        target = find_article_by_id(Path(settings.CURATED_DIR), article_id)
        paths = [target] if target else []
    else:
        root = Path(settings.CURATED_DIR)
        if domain:
            root = root / domain
        paths = sorted(root.rglob("*.md")) if root.exists() else []

    outcomes: list[ArticleTagOutcome] = []
    for path in paths:
        try:
            outcome = _tag_one(settings, path, competencies, name, proposer)
        except Exception as exc:  # noqa: BLE001
            logger.warning("curriculum tag: failed %s — %s", path, exc)
            outcomes.append(
                ArticleTagOutcome(
                    path=path,
                    article_id=path.stem,
                    previous_codes=[],
                    new_codes=[],
                    skipped_reason=str(exc),
                )
            )
            continue
        outcomes.append(outcome)
    return outcomes


__all__ = ["TagResult", "TagDeps", "ArticleTagOutcome", "tag_articles"]
