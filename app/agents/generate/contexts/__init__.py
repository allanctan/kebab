"""Contexts subpackage — vertical-specific metadata classification.

Verticals are defined in YAML files under ``.kebab/<vertical>.yaml``.
No Python classes per vertical — the YAML defines description,
generate_instruction, authoritative_sources, and classification_fields.

The LLM selector picks the best vertical based on article content,
then the LLM classifier populates the fields defined in the YAML.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import BaseModel, Field, create_model
from pydantic_ai import Agent

from app.config.config import Settings
from app.core.llm.resolve import resolve_model
from app.core.markdown import read_article, write_article

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# YAML-based vertical loading
# ---------------------------------------------------------------------------


@dataclass
class VerticalConfig:
    """Loaded from .kebab/<vertical>.yaml."""

    key: str
    description: str
    generate_instruction: str
    authoritative_sources: list[str]
    classification_fields: dict[str, Any]


def _load_verticals(settings: Settings) -> dict[str, VerticalConfig]:
    """Load all .kebab/<name>.yaml files as vertical configs."""
    kebab_dir = Path(settings.KNOWLEDGE_DIR) / ".kebab"
    verticals: dict[str, VerticalConfig] = {}
    if not kebab_dir.exists():
        return verticals
    for yaml_path in sorted(kebab_dir.glob("*.yaml")):
        key = yaml_path.stem
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("contexts: failed to load %s: %s", yaml_path, exc)
            continue
        verticals[key] = VerticalConfig(
            key=key,
            description=data.get("description", ""),
            generate_instruction=data.get("generate_instruction", ""),
            authoritative_sources=data.get("authoritative_sources", []),
            classification_fields=data.get("classification_fields", {}),
        )
    return verticals


def _build_pydantic_model(vertical: VerticalConfig) -> type[BaseModel]:
    """Dynamically build a Pydantic model from classification_fields YAML."""
    fields: dict[str, Any] = {}
    for name, spec in vertical.classification_fields.items():
        field_type = spec.get("type", "str")
        description = spec.get("description", "")
        default = spec.get("default", ...)

        if field_type == "int":
            py_type = int
        elif field_type == "list":
            py_type = list[str]
            if default is ...:
                default = []
        elif field_type == "enum":
            # For dynamic enums, use str with description listing valid values
            py_type = str
        else:
            py_type = str

        if default is ...:
            fields[name] = (py_type, Field(..., description=description))
        else:
            fields[name] = (py_type, Field(default=default, description=description))

    model = create_model(
        f"{vertical.key.title()}Context",
        **fields,
    )
    return model


def load_vertical_config(settings: Settings, vertical_key: str) -> VerticalConfig | None:
    """Load a single vertical config by key."""
    verticals = _load_verticals(settings)
    return verticals.get(vertical_key)


# ---------------------------------------------------------------------------
# LLM-based vertical selection and field classification
# ---------------------------------------------------------------------------


def _select_vertical(
    settings: Settings,
    article_name: str,
    body_excerpt: str,
    source_metadata: list[dict[str, str]] | None = None,
) -> str:
    """Use LLM to select the most appropriate vertical key for an article."""
    verticals = _load_verticals(settings)
    if not verticals:
        return "education"

    descriptions = "\n".join(
        f"- {key}: {v.description.strip()}"
        for key, v in verticals.items()
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

    if result in verticals:
        return result
    for key in verticals:
        if key in result:
            return key

    logger.warning("contexts: LLM returned unknown vertical %r — defaulting to education", result)
    return "education"


def _classify_fields(
    settings: Settings,
    vertical: VerticalConfig,
    article_name: str,
    body_excerpt: str,
    source_metadata: list[dict[str, str]],
) -> dict[str, Any]:
    """Use LLM to classify the vertical-specific fields."""
    model_cls = _build_pydantic_model(vertical)

    field_descriptions = "\n".join(
        f"- {name}: {field.description}"
        for name, field in model_cls.model_fields.items()
    )

    agent = Agent(
        model=resolve_model(settings.CONTEXTS_MODEL),
        output_type=model_cls,
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
    result = agent.run_sync("\n".join(parts)).output
    return result.model_dump()


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------


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


VerticalProposer = Callable[[Settings, ContextDeps, str], dict[str, Any]]


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
    proposer: VerticalProposer | None = None,
    article_paths: list[Path] | None = None,
) -> ContextsResult:
    """Classify vertical-specific context metadata for articles.

    The LLM selects the best vertical per article from the available
    .kebab/<vertical>.yaml files, then classifies the fields defined
    in that vertical's classification_fields.
    """
    updated: list[Path] = []
    skipped: list[tuple[Path, str]] = []

    paths = article_paths if article_paths is not None else _iter_articles(Path(settings.CURATED_DIR))
    for path in paths:
        try:
            fm, body, _ = read_article(path)
        except Exception as exc:  # noqa: BLE001
            skipped.append((path, str(exc)))
            continue

        source_meta = _load_source_metadata(settings, fm)
        body_excerpt = body[:2000]

        if proposer is not None:
            deps = ContextDeps(
                settings=settings,
                article_id=fm.id,
                article_name=fm.name,
                body_excerpt=body_excerpt,
                source_metadata=source_meta,
            )
            try:
                context = proposer(settings, deps, "education")
            except Exception as exc:  # noqa: BLE001
                skipped.append((path, f"proposer failed: {exc}"))
                continue
            vertical_key = "education"
            context_dict = context if isinstance(context, dict) else context.model_dump()
        else:
            try:
                vertical_key = _select_vertical(settings, fm.name, body_excerpt, source_meta)
            except Exception as exc:  # noqa: BLE001
                skipped.append((path, f"vertical selection failed: {exc}"))
                continue

            vertical = load_vertical_config(settings, vertical_key)
            if vertical is None or not vertical.classification_fields:
                skipped.append((path, f"no classification fields for {vertical_key}"))
                continue

            try:
                context_dict = _classify_fields(
                    settings, vertical, fm.name, body_excerpt, source_meta,
                )
            except Exception as exc:  # noqa: BLE001
                skipped.append((path, f"classification failed: {exc}"))
                continue

        fm_dump: dict[str, Any] = fm.model_dump()
        contexts = dict(fm_dump.get("contexts") or {})
        contexts[vertical_key] = context_dict
        fm_dump["contexts"] = contexts
        write_article(path, type(fm).model_validate(fm_dump), body)
        updated.append(path)
        logger.info("contexts: %s → %s", fm.id, vertical_key)

        from app.core.audit import log_event
        log_event(
            path, stage="contexts", action="context_classified",
            article_id=fm.id,
            vertical=vertical_key,
            fields=str(context_dict),
        )

    logger.info("contexts: updated %d, skipped %d", len(updated), len(skipped))
    return ContextsResult(updated=updated, skipped=skipped)
