"""Contexts subpackage — vertical-specific metadata classification.

Each vertical is a separate module with DESCRIPTION, BASE_INSTRUCTION,
and VERTICAL_KEY as ClassVars. The LLM selector picks the best vertical
based on article content.
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

from app.agents.generate.contexts.education import EducationContext as EducationContext
from app.agents.generate.contexts.healthcare import HealthcareContext as HealthcareContext
from app.agents.generate.contexts.legal import LegalContext as LegalContext
from app.agents.generate.contexts.policy import PolicyContext as PolicyContext

logger = logging.getLogger(__name__)

# Registry of available verticals — add new ones here.
VERTICALS: dict[str, type[BaseModel]] = {
    "education": EducationContext,
    "healthcare": HealthcareContext,
    "policy": PolicyContext,
    "legal": LegalContext,
}


def _select_vertical(
    settings: Settings,
    article_name: str,
    body_excerpt: str,
    source_metadata: list[dict[str, str]] | None = None,
) -> type[BaseModel]:
    """Use LLM to select the most appropriate vertical for an article."""
    descriptions = "\n".join(
        f"- {key}: {getattr(cls, 'DESCRIPTION', '')}"
        for key, cls in VERTICALS.items()
    )

    agent = Agent(
        model=resolve_model(settings.CONTEXTS_MODEL),
        output_type=str,
        system_prompt=(
            "You classify articles into the most appropriate vertical category.\n\n"
            "Available verticals:\n"
            f"{descriptions}\n\n"
            "Return ONLY the vertical key (e.g. 'education', 'legal', 'healthcare', 'policy'). "
            "Nothing else."
        ),
        retries=settings.LLM_MAX_RETRIES,
    )
    parts = [f"Article: {article_name}", f"\n{body_excerpt}"]
    if source_metadata:
        parts.append(f"\nsource_metadata: {source_metadata}")
    result = agent.run_sync("\n".join(parts)).output.strip().lower()

    if result in VERTICALS:
        return VERTICALS[result]

    # Fallback: try matching partial key
    for key, cls in VERTICALS.items():
        if key in result:
            return cls

    logger.warning("contexts: LLM returned unknown vertical %r — defaulting to education", result)
    return EducationContext


def _classify_fields(
    settings: Settings,
    context_cls: type[BaseModel],
    article_name: str,
    body_excerpt: str,
    source_metadata: list[dict[str, str]],
) -> BaseModel:
    """Use LLM to classify the vertical-specific fields."""
    # Build a prompt from the field descriptions
    field_descriptions = "\n".join(
        f"- {name}: {field.description}"
        for name, field in context_cls.model_fields.items()
    )

    agent = Agent(
        model=resolve_model(settings.CONTEXTS_MODEL),
        output_type=context_cls,
        system_prompt=(
            f"Classify this article's metadata fields.\n\n"
            f"## Fields to populate\n{field_descriptions}\n\n"
            f"If source_metadata provides values, use them directly. "
            f"Otherwise, infer from the article content."
        ),
        retries=settings.LLM_MAX_RETRIES,
    )
    parts = [f"article_name: {article_name}", f"\nbody_excerpt:\n{body_excerpt}"]
    if source_metadata:
        parts.append(f"\nsource_metadata: {source_metadata}")
    return agent.run_sync("\n".join(parts)).output


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


def _iter_articles(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.md"))


def _load_source_metadata(settings: Settings, fm: Any) -> list[dict[str, str]]:
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
    """Classify vertical-specific context metadata for articles.

    If ``context_cls`` is None, the LLM selects the best vertical per article.
    """
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
        body_excerpt = body[:2000]

        # Select vertical — either provided or LLM-selected
        if context_cls is not None:
            selected_cls = context_cls
        elif proposer is not None:
            # Legacy path: proposer handles everything
            deps = ContextDeps(
                settings=settings,
                article_id=fm.id,
                article_name=fm.name,
                body_excerpt=body_excerpt,
                source_metadata=source_meta,
            )
            try:
                context = proposer(settings, deps, EducationContext)
            except Exception as exc:  # noqa: BLE001
                skipped.append((path, f"proposer failed: {exc}"))
                continue
            vertical_key = getattr(type(context), "VERTICAL_KEY", "default")
            fm_dump: dict[str, Any] = fm.model_dump()
            contexts = dict(fm_dump.get("contexts") or {})
            contexts[vertical_key] = context.model_dump() if hasattr(context, "model_dump") else context
            fm_dump["contexts"] = contexts
            write_article(path, type(fm).model_validate(fm_dump), body)
            updated.append(path)
            continue
        else:
            try:
                selected_cls = _select_vertical(settings, fm.name, body_excerpt, source_meta)
            except Exception as exc:  # noqa: BLE001
                skipped.append((path, f"vertical selection failed: {exc}"))
                continue

        vertical_key = getattr(selected_cls, "VERTICAL_KEY", "default")

        # Classify fields
        try:
            context = _classify_fields(
                settings, selected_cls, fm.name, body_excerpt, source_meta,
            )
        except Exception as exc:  # noqa: BLE001
            skipped.append((path, f"classification failed: {exc}"))
            continue

        fm_dump = fm.model_dump()
        contexts = dict(fm_dump.get("contexts") or {})
        contexts[vertical_key] = context.model_dump() if hasattr(context, "model_dump") else context
        fm_dump["contexts"] = contexts
        write_article(path, type(fm).model_validate(fm_dump), body)
        updated.append(path)
        logger.info("contexts: %s → %s", fm.id, vertical_key)

    logger.info("contexts: updated %d, skipped %d", len(updated), len(skipped))
    return ContextsResult(updated=updated, skipped=skipped)
