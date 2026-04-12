"""Stage 4 — generate: LLM creates grounded markdown for gap articles.

Reads the latest gaps report (from Stage 3), loads candidate raw sources,
calls the curation agent with a strict ``GenerationResult`` schema that
enforces ``source_ids: min_length=1`` (no source, no save), and writes the
article to the path organize reserved (or a sensible default).

The token-limit gate is enforced **inside** this stage — articles
exceeding ``MAX_TOKENS_PER_ARTICLE`` are skipped, not silently truncated.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai import Agent

from app.config.config import Settings
from app.core.errors import KebabError
from app.core.images.figures import (
    FigureEntry,
    FigureManifest,
    copy_figures,
    load_figure_manifest,
    resolve_figure_markers,
)
from app.core.llm.resolve import resolve_model
from app.core.markdown import read_article, write_article
from app.agents.organize.agent import HierarchyNode, HierarchyPlan
from app.core.sources.index import SourceEntry, SourceIndex, load_index
from app.core.llm.tokens import count_tokens
from app.models.frontmatter import FrontmatterSchema
from app.models.source import Source
from app.agents.generate.gaps import Gap, GapReport, latest_gaps
from app.agents.organize import load_plan

logger = logging.getLogger(__name__)


_PROMPT_PATH = Path(__file__).parent / "prompts" / "generate_system.md"


class GenerationResult(BaseModel):
    """Output schema for the generate agent.

    Field order is intentional: ``reasoning`` first nudges the LLM to
    write its analysis before committing to the structured fields.
    """

    model_config = ConfigDict(extra="forbid")

    reasoning: str = Field(
        ...,
        description="Brief analysis of the source material: what key concepts are "
        "covered, how to structure the article, and what to omit because the "
        "sources don't support it.",
    )
    description: str = Field(..., description="One-sentence article summary.")
    body: str = Field(..., description="Markdown body, no frontmatter.")
    keywords: list[str] = Field(
        default_factory=list,
        description="5–8 search terms. Mix of technical terms and plain-language "
        "phrases a reader might search. Must not duplicate the article name or "
        "description — those are indexed separately.",
    )
    summary: str = Field(
        ..., description="2-3 sentence scope statement: what the article covers and its boundaries."
    )
    source_ids: list[int] = Field(
        ..., min_length=1, description="Local footnote numbers cited in the body."
    )


@dataclass
class GenerateDeps:
    """Runtime context for one generate call."""

    settings: Settings
    gap: Gap
    sources: list[tuple[str, str]]


@dataclass
class GenerateResult:
    """Summary of a single generate run."""

    written: list[Path]
    skipped: list[tuple[str, str]]


GenerateProposer = Callable[[Settings, Gap, list[tuple[str, str]]], GenerationResult]


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def build_generate_agent(settings: Settings) -> Agent[GenerateDeps, GenerationResult]:
    return Agent(
        model=resolve_model(settings.GENERATE_MODEL),
        deps_type=GenerateDeps,
        output_type=GenerationResult,
        system_prompt=_load_prompt(),
        retries=settings.LLM_MAX_RETRIES,
    )


def _load_figures(
    settings: Settings,
    gap: Gap,
    index: SourceIndex,
) -> FigureManifest:
    """Load and merge figure manifests from all sources of a gap."""
    processed_docs = Path(settings.PROCESSED_DIR) / "documents"
    all_entries: list[FigureEntry] = []
    num = 1
    for source_id in gap.source_files:
        try:
            entry = index.get(source_id)
        except KeyError:
            continue
        doc_dir = processed_docs / entry.stem
        manifest = load_figure_manifest(doc_dir)
        for fig in manifest.entries:
            all_entries.append(FigureEntry(
                local_num=num,
                figure_id=fig.figure_id,
                description=fig.description,
                source_path=fig.source_path,
                mime_type=fig.mime_type,
            ))
            num += 1
    return FigureManifest(entries=all_entries)


def _default_proposer(
    settings: Settings,
    gap: Gap,
    sources: list[tuple[str, str]],
    figure_manifest: FigureManifest | None = None,
    base_instruction: str | None = None,
    source_metadata: dict[str, str] | None = None,
) -> GenerationResult:
    agent = build_generate_agent(settings)
    deps = GenerateDeps(settings=settings, gap=gap, sources=sources)
    sources_str = "\n\n".join(f"### {name}\n{snippet}" for name, snippet in sources)
    parts = [
        f"topic_id: {gap.id}",
        f"topic_name: {gap.name}",
        f"topic_description: {gap.description}",
    ]
    if base_instruction:
        # Format placeholders like {grade}, {subject} with source metadata.
        # Missing keys are left as-is (e.g. "{grade}" when no metadata).
        meta = source_metadata or {}
        instruction = base_instruction.format_map(defaultdict(str, meta))
        parts.append(f"\nContent instruction: {instruction}")
    parts.append(f"\nsources:\n{sources_str}")
    if figure_manifest and figure_manifest.entries:
        parts.append(f"\n{figure_manifest.prompt_text()}")
    user = "\n".join(parts)
    return agent.run_sync(user, deps=deps).output


def _append_footnotes(
    body: str,
    local_to_entry: dict[int, SourceEntry],
    article_path: Path,
    knowledge_root: Path,
) -> str:
    """Append Obsidian footnote definitions to the article body.

    ``knowledge_root`` is the absolute path to ``settings.KNOWLEDGE_DIR``.
    Footnote URLs are computed as ``knowledge_root / entry.raw_path``
    expressed relative to the article's parent directory.
    """
    lines: list[str] = []
    for local_num in sorted(local_to_entry):
        entry = local_to_entry[local_num]
        raw_path = knowledge_root / entry.raw_path
        rel = raw_path.relative_to(article_path.parent, walk_up=True)
        encoded = str(rel).replace(" ", "%20")
        lines.append(f"[^{local_num}]: [{entry.id}] [{entry.title}]({encoded})")

    if not lines:
        return body
    return body.rstrip() + "\n\n" + "\n".join(lines) + "\n"


def _load_sources(
    settings: Settings,
    gap: Gap,
    index: SourceIndex,
) -> list[tuple[int, SourceEntry, str]]:
    """Resolve gap's source IDs to (local_num, entry, text_content) triples."""
    processed_docs = Path(settings.PROCESSED_DIR) / "documents"
    out: list[tuple[int, SourceEntry, str]] = []
    for local_num, source_id in enumerate(gap.source_files, start=1):
        try:
            entry = index.get(source_id)
        except KeyError:
            logger.warning("generate: source id %d not in index — skipping", source_id)
            continue
        candidates = [
            processed_docs / entry.stem / "text.md",
            Path(settings.PROCESSED_DIR) / "web" / f"{entry.stem}.md",
        ]
        for text_path in candidates:
            if text_path.exists():
                text = text_path.read_text(encoding="utf-8")[:8000]
                out.append((local_num, entry, text))
                break
    return out


def _output_path(settings: Settings, gap: Gap) -> Path:
    """Return the canonical markdown path for ``gap``.

    The gap carries a ``target_path`` reserved by the organize stage.
    If that's missing (e.g. a hand-crafted gap), fall back to a flat
    ``<KNOWLEDGE_DIR>/<id>.md``.
    """
    if gap.target_path:
        return Path(gap.target_path)
    knowledge = Path(settings.KNOWLEDGE_DIR)
    knowledge.mkdir(parents=True, exist_ok=True)
    return knowledge / f"{gap.id}.md"


def _parent_ids_for(plan: HierarchyPlan | None, article_id: str) -> list[str]:
    """Return the chain of parent IDs for ``article_id`` from the plan.

    Ordered from immediate parent up to the root domain. Returns an empty
    list if the article is not in the plan or has no parent chain.
    """
    if plan is None:
        return []
    by_id = {node.id: node for node in plan.nodes}
    chain: list[str] = []
    cursor: HierarchyNode | None = by_id.get(article_id)
    if cursor is None:
        return []
    while cursor.parent_id is not None:
        parent = by_id.get(cursor.parent_id)
        if parent is None:
            break
        chain.append(parent.id)
        cursor = parent
    return chain


def _preserve_existing_fields(target_path: Path) -> dict[str, object]:
    """Return fields from an existing article that should survive regeneration.

    When regenerating a stale article, we want to keep verifications,
    human_verified flags, and the existing frontmatter extras that aren't
    about to be overwritten. If the file doesn't exist yet (fresh gap),
    return an empty dict.
    """
    if not target_path.exists():
        return {}
    try:
        fm, _body, _ = read_article(target_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate: could not parse existing frontmatter at %s: %s", target_path, exc
        )
        return {}
    preserved: dict[str, object] = {}
    dump = fm.model_dump()
    for key in ("verifications", "human_verified", "human_verified_by", "human_verified_at"):
        if dump.get(key):
            preserved[key] = dump[key]
    return preserved


def write_articles(
    settings: Settings,
    *,
    domain: str = "default",
    gaps: GapReport | None = None,
    proposer: GenerateProposer = _default_proposer,
    plan: HierarchyPlan | None = None,
) -> GenerateResult:
    """Execute the generate stage. Returns paths written and skipped reasons.

    If ``plan`` is not provided, the cached plan is loaded from disk so
    that ``parent_ids`` can be stamped into article frontmatter. Without a
    plan, every generated article is flagged as an orphan by the lint
    agent — hence the automatic load.
    """
    report = gaps if gaps is not None else latest_gaps(settings)
    if report is None:
        raise KebabError("generate: no gaps report — run `kebab gaps` first")

    plan = plan if plan is not None else load_plan(settings, domain)
    index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    index = load_index(index_path)
    written: list[Path] = []
    skipped: list[tuple[str, str]] = []

    for gap in report.gaps:
        source_triples = _load_sources(settings, gap, index)
        if not source_triples:
            skipped.append((gap.id, "no source files found"))
            continue

        sources_for_llm: list[tuple[str, str]] = [
            (f"[^{local_num}] {entry.title}", text)
            for local_num, entry, text in source_triples
        ]

        figure_manifest = _load_figures(settings, gap, index)

        # Derive BASE_INSTRUCTION from vertical selection.
        # On a fresh run the article has no context yet, so we select the
        # vertical from the source text — the same snippets the writer is
        # about to send to the LLM. This avoids the chicken-and-egg: the
        # writer needs the instruction before writing, but the full context
        # classifier needs the written body.
        base_instruction: str | None = None
        target = _output_path(settings, gap)

        # First try: read from existing context on disk (re-generation case)
        if target.exists():
            try:
                _fm, _body, _ = read_article(target)
                fm_extras = _fm.model_dump()
                article_contexts = fm_extras.get("contexts", {})
                from app.agents.generate.contexts import VERTICALS
                for vkey, vcls in VERTICALS.items():
                    if vkey in article_contexts:
                        base_instruction = getattr(vcls, "BASE_INSTRUCTION", None)
                        break
            except Exception:  # noqa: BLE001
                pass

        # Fallback: select vertical from source text (fresh generation case)
        if base_instruction is None:
            try:
                from app.agents.generate.contexts import _select_vertical
                source_excerpt = "\n".join(text[:500] for _, text in sources_for_llm)
                vertical_cls = _select_vertical(
                    settings, gap.name, source_excerpt,
                )
                base_instruction = getattr(vertical_cls, "BASE_INSTRUCTION", None)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "generate: vertical selection from sources failed for %s: %s",
                    gap.id, exc,
                )

        # Collect source metadata (grade, subject, etc.) from source entries.
        # Merge across sources — first non-empty value wins per key.
        merged_meta: dict[str, str] = {}
        for _, entry, _ in source_triples:
            for k, v in entry.metadata.items():
                if k not in merged_meta and v:
                    merged_meta[k] = v

        try:
            if proposer is _default_proposer:
                result = _default_proposer(
                    settings, gap, sources_for_llm, figure_manifest,
                    base_instruction, merged_meta or None,
                )
            else:
                result = proposer(settings, gap, sources_for_llm)
        except ValidationError as exc:
            skipped.append((gap.id, f"schema violation: {exc}"))
            continue
        if count_tokens(result.body) > settings.MAX_TOKENS_PER_ARTICLE:
            skipped.append((gap.id, f"body exceeds {settings.MAX_TOKENS_PER_ARTICLE} tokens"))
            continue

        path = _output_path(settings, gap)
        path.parent.mkdir(parents=True, exist_ok=True)
        preserved = _preserve_existing_fields(path)
        parent_ids = _parent_ids_for(plan, gap.id)

        local_to_entry: dict[int, SourceEntry] = {
            local_num: entry for local_num, entry, _text in source_triples
        }
        fm_sources = [
            Source.model_validate(
                {
                    "id": entry.id,
                    "title": entry.title,
                    "tier": entry.tier,
                    "checksum": entry.checksum,
                    "adapter": entry.adapter,
                    "retrieved_at": entry.retrieved_at,
                }
            )
            for entry in local_to_entry.values()
        ]

        fm = FrontmatterSchema(
            id=gap.id,
            name=gap.name,
            type="article",
            sources=fm_sources,
        )
        fm_dump = fm.model_dump()
        fm_dump["description"] = result.description
        fm_dump["keywords"] = result.keywords
        fm_dump["summary"] = result.summary
        fm_dump["parent_ids"] = parent_ids
        for key, value in preserved.items():
            fm_dump[key] = value
        fm = FrontmatterSchema.model_validate(fm_dump)

        body = _append_footnotes(
            result.body, local_to_entry, path, Path(settings.KNOWLEDGE_DIR)
        )

        article_slug = path.stem
        body_with_figures, used_figures = resolve_figure_markers(
            body, figure_manifest, article_slug,
        )
        if used_figures:
            figures_dest = path.parent / "figures" / article_slug
            copy_figures(used_figures, figures_dest)
        body = body_with_figures

        write_article(path, fm, body)
        written.append(path)

        from app.core.audit import log_event
        log_event(
            path, stage="generate", action="article_written",
            article_id=gap.id,
            detail=f"Generated from {len(source_triples)} source(s), {len(used_figures)} figure(s)",
        )

    logger.info("generate: wrote %d, skipped %d", len(written), len(skipped))
    return GenerateResult(written=written, skipped=skipped)
