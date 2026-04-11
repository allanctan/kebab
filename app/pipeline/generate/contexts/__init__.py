"""Contexts subpackage — vertical-specific metadata classification.

Each vertical is a separate module with a self-contained BaseModel
carrying SYSTEM_PROMPT and VERTICAL_KEY as ClassVars.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel
from pydantic_ai import Agent

from app.config.config import Settings
from app.core.llm.resolve import resolve_model
from app.core.markdown import read_article, write_article

from app.pipeline.generate.contexts.education import EducationContext as EducationContext
from app.pipeline.generate.contexts.healthcare import HealthcareContext as HealthcareContext
from app.pipeline.generate.contexts.legal import LegalContext as LegalContext
from app.pipeline.generate.contexts.policy import PolicyContext as PolicyContext

logger = logging.getLogger(__name__)

# Registry of available verticals — add new ones here.
VERTICALS: dict[str, type[BaseModel]] = {
    "education": EducationContext,
    "healthcare": HealthcareContext,
    "policy": PolicyContext,
    "legal": LegalContext,
}


@dataclass
class ContextDeps:
    settings: Settings
    article_id: str
    article_name: str
    body_excerpt: str
    source_metadata: list[dict[str, str]]


@dataclass
class ContextsResult:
    updated: list[Path]
    skipped: list[tuple[Path, str]]


VerticalProposer = Callable[[Settings, ContextDeps, type[BaseModel]], BaseModel]


def _build_agent(settings: Settings, context_cls: type[BaseModel]) -> Agent[ContextDeps, Any]:
    system_prompt: str = getattr(context_cls, "SYSTEM_PROMPT", "")
    return Agent(
        model=resolve_model(settings.CONTEXTS_MODEL),
        deps_type=ContextDeps,
        output_type=context_cls,
        system_prompt=system_prompt,
        retries=settings.LLM_MAX_RETRIES,
    )


def _default_proposer(
    settings: Settings, deps: ContextDeps, context_cls: type[BaseModel]
) -> BaseModel:
    agent = _build_agent(settings, context_cls)
    parts = [
        f"article_name: {deps.article_name}",
        f"\nbody_excerpt:\n{deps.body_excerpt}",
    ]
    if deps.source_metadata:
        parts.append(f"\nsource_metadata: {deps.source_metadata}")
    user = "\n".join(parts)
    return agent.run_sync(user, deps=deps).output


def _iter_articles(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.md"))


def _load_source_metadata(
    settings: Settings, fm: Any,
) -> list[dict[str, str]]:
    from app.core.sources.index import load_index

    index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    index = load_index(index_path)
    out: list[dict[str, str]] = []
    for source in getattr(fm, "sources", []):
        source_id = getattr(source, "id", None)
        if source_id is None:
            continue
        try:
            entry = index.get(source_id)
            out.append(entry.metadata)
        except KeyError:
            pass
    return out


def run(
    settings: Settings,
    *,
    context_cls: type[BaseModel] | None = None,
    proposer: VerticalProposer | None = None,
    article_paths: list[Path] | None = None,
) -> ContextsResult:
    """Classify vertical-specific context metadata for articles."""
    if context_cls is None:
        vertical_name = getattr(settings, "CONTEXT_VERTICAL", "education") or "education"
        context_cls = VERTICALS.get(vertical_name, EducationContext)

    vertical_key: str = getattr(context_cls, "VERTICAL_KEY", "default")
    proposer_fn = proposer or _default_proposer

    updated: list[Path] = []
    skipped: list[tuple[Path, str]] = []

    paths = article_paths if article_paths is not None else _iter_articles(Path(settings.CURATED_DIR))
    for path in paths:
        try:
            fm, body = read_article(path)
        except Exception as exc:  # noqa: BLE001
            skipped.append((path, str(exc)))
            continue
        source_meta = _load_source_metadata(settings, fm)
        deps = ContextDeps(
            settings=settings,
            article_id=fm.id,
            article_name=fm.name,
            body_excerpt=body[:2000],
            source_metadata=source_meta,
        )
        try:
            context = proposer_fn(settings, deps, context_cls)
        except Exception as exc:  # noqa: BLE001
            skipped.append((path, f"proposer failed: {exc}"))
            continue

        fm_dump: dict[str, Any] = fm.model_dump()
        contexts = dict(fm_dump.get("contexts") or {})
        contexts[vertical_key] = context.model_dump() if hasattr(context, "model_dump") else context
        fm_dump["contexts"] = contexts
        write_article(path, type(fm).model_validate(fm_dump), body)
        updated.append(path)

    logger.info("contexts: updated %d, skipped %d", len(updated), len(skipped))
    return ContextsResult(updated=updated, skipped=skipped)
