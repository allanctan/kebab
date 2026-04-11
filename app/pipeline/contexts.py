"""Stage 5 — contexts: populate vertical-specific filter data.

Each vertical defines a context model (a Pydantic BaseModel subclass) with
ClassVar fields for ``SYSTEM_PROMPT`` and ``VERTICAL_KEY``. The stage is
parametrized by passing the context class — no hardcoded verticals in the
pipeline logic.

The K-12 Philippines pilot is the first vertical: ``EducationContext``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.config.config import Settings
from app.core.llm.resolve import resolve_model
from app.core.markdown import read_article, write_article

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vertical context models
# ---------------------------------------------------------------------------


class EducationContext(BaseModel):
    """K-12 Philippines context for the pilot vertical."""

    model_config = ConfigDict(extra="forbid")

    SYSTEM_PROMPT: ClassVar[str] = (
        "You classify educational articles by grade level and subject.\n\n"
        "## Input\n"
        "- `article_name`: title of the article.\n"
        "- `body_excerpt`: first ~2000 chars of the article body.\n"
        "- `source_metadata`: metadata extracted from source file paths. "
        "If `grade` and `subject` fields are present, use them directly.\n\n"
        "## Output\n"
        "- `grade`: integer 1–12 — the recommended grade for this material.\n"
        "- `subject`: academic subject (e.g. 'science', 'mathematics', 'english').\n"
        "- `language`: ISO 639-1 code (default \"en\").\n\n"
        "If source_metadata provides grade and subject, use those exact values. "
        "Otherwise, infer from the article content."
    )
    VERTICAL_KEY: ClassVar[str] = "education"

    grade: int = Field(..., ge=1, le=12, description="Recommended K-12 grade level.")
    subject: str = Field(..., description="Academic subject (e.g. science, mathematics, english).")
    language: str = Field(default="en", description="ISO 639-1 language code.")


class HealthcareContext(BaseModel):
    """Healthcare vertical — clinical and medical content."""

    model_config = ConfigDict(extra="forbid")

    SYSTEM_PROMPT: ClassVar[str] = (
        "You classify healthcare articles by evidence grade and specialty.\n\n"
        "## Input\n"
        "- `article_name`: title of the article.\n"
        "- `body_excerpt`: first ~2000 chars of the article body.\n\n"
        "## Output\n"
        "- `evidence_grade`: GRADE scale — one of 'high', 'moderate', 'low', 'very_low'.\n"
        "- `specialty`: medical specialty (e.g. 'cardiology', 'oncology', 'general').\n"
        "- `audience`: intended reader — one of 'clinician', 'patient', 'researcher'.\n\n"
        "Base evidence_grade on the strength of cited sources: systematic reviews "
        "and RCTs are 'high', observational studies 'moderate', expert opinion 'low'."
    )
    VERTICAL_KEY: ClassVar[str] = "healthcare"

    evidence_grade: str = Field(
        ..., description="GRADE evidence level: high, moderate, low, very_low."
    )
    specialty: str = Field(
        default="general", description="Medical specialty (e.g. cardiology, oncology)."
    )
    audience: str = Field(
        default="clinician",
        description="Intended audience: clinician, patient, or researcher.",
    )


class PolicyContext(BaseModel):
    """Policy vertical — regulatory, compliance, and governance content."""

    model_config = ConfigDict(extra="forbid")

    SYSTEM_PROMPT: ClassVar[str] = (
        "You classify policy and regulatory articles by jurisdiction and status.\n\n"
        "## Input\n"
        "- `article_name`: title of the article.\n"
        "- `body_excerpt`: first ~2000 chars of the article body.\n\n"
        "## Output\n"
        "- `jurisdiction`: governing body or region (e.g. 'PH', 'US-FDA', 'EU').\n"
        "- `policy_version`: version or year of the policy (e.g. '2024', 'v3.1').\n"
        "- `status`: one of 'active', 'draft', 'superseded', 'archived'.\n\n"
        "Infer jurisdiction from the content. If unclear, use 'unknown'."
    )
    VERTICAL_KEY: ClassVar[str] = "policy"

    jurisdiction: str = Field(
        default="unknown", description="Governing body or region (e.g. PH, US-FDA, EU)."
    )
    policy_version: str = Field(
        default="unknown", description="Policy version or year."
    )
    status: str = Field(
        default="active",
        description="Policy status: active, draft, superseded, archived.",
    )


class LegalContext(BaseModel):
    """Legal vertical — statutes, case law, and legal analysis."""

    model_config = ConfigDict(extra="forbid")

    SYSTEM_PROMPT: ClassVar[str] = (
        "You classify legal articles by jurisdiction, area of law, and authority.\n\n"
        "## Input\n"
        "- `article_name`: title of the article.\n"
        "- `body_excerpt`: first ~2000 chars of the article body.\n\n"
        "## Output\n"
        "- `jurisdiction`: legal jurisdiction (e.g. 'PH', 'US-Federal', 'UK', 'EU').\n"
        "- `area_of_law`: primary area (e.g. 'criminal', 'civil', 'labor', "
        "'constitutional', 'commercial', 'environmental').\n"
        "- `authority`: source authority — one of 'statute', 'case_law', "
        "'regulation', 'commentary', 'opinion'.\n"
        "- `year`: year of enactment or decision, if identifiable. Use null if unknown.\n\n"
        "Infer jurisdiction from the content. If unclear, use 'unknown'."
    )
    VERTICAL_KEY: ClassVar[str] = "legal"

    jurisdiction: str = Field(
        default="unknown", description="Legal jurisdiction (e.g. PH, US-Federal, UK)."
    )
    area_of_law: str = Field(
        default="general", description="Primary area of law (e.g. criminal, civil, labor)."
    )
    authority: str = Field(
        default="commentary",
        description="Source authority: statute, case_law, regulation, commentary, opinion.",
    )
    year: int | None = Field(
        default=None, description="Year of enactment or decision, if known."
    )


# Registry of available verticals — add new ones here.
VERTICALS: dict[str, type[BaseModel]] = {
    "education": EducationContext,
    "healthcare": HealthcareContext,
    "policy": PolicyContext,
    "legal": LegalContext,
}


# ---------------------------------------------------------------------------
# Stage machinery
# ---------------------------------------------------------------------------


@dataclass
class ContextDeps:
    settings: Settings
    article_id: str
    article_name: str
    body_excerpt: str
    source_metadata: list[dict[str, str]]
    """Metadata dicts from source index entries for this article's sources."""


@dataclass
class ContextsResult:
    updated: list[Path]
    skipped: list[tuple[Path, str]]


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


VerticalProposer = Callable[[Settings, ContextDeps, type[BaseModel]], BaseModel]


def _iter_articles(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.md"))


def _load_source_metadata(
    settings: Settings, fm: Any,
) -> list[dict[str, str]]:
    """Return metadata dicts from the source index for an article's sources."""
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
) -> ContextsResult:
    """Run the contexts stage on every article in ``CURATED_DIR``.

    ``context_cls`` determines the vertical. If not provided, looks up
    ``settings.CONTEXT_VERTICAL`` in the ``VERTICALS`` registry (defaults
    to ``education``).

    Source metadata from the index (extracted via ``SOURCE_PATH_PATTERN``)
    is passed to the proposer in ``ContextDeps.source_metadata``. This
    lets verticals use folder-structure data (grade, subject, etc.) as
    strong signals instead of relying purely on LLM inference.
    """
    if context_cls is None:
        vertical_name = getattr(settings, "CONTEXT_VERTICAL", "education") or "education"
        context_cls = VERTICALS.get(vertical_name, EducationContext)

    vertical_key: str = getattr(context_cls, "VERTICAL_KEY", "default")
    proposer_fn = proposer or _default_proposer

    updated: list[Path] = []
    skipped: list[tuple[Path, str]] = []

    for path in _iter_articles(Path(settings.CURATED_DIR)):
        try:
            fm, body = read_article(path)
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the loop
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
