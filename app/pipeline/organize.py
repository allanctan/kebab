"""Stage 1 — organize: canonical taxonomy owner.

Organize is the **single source of truth** for what belongs in the
knowledge base and where each article lives on disk. Its output is:

1. ``knowledge/.kebab/plan.json`` — a persisted :class:`HierarchyPlan`
   with the resolved ``md_path`` on every article node.
2. Empty markdown stubs at those paths so generate/contexts/verify have
   something to write to.

Re-running ``organize`` is a **no-op** when ``plan.json`` exists —
Gemini's taxonomy is non-deterministic, so blindly re-running produced
parallel trees with conflicting IDs. Pass ``force=True`` to re-propose
from scratch (and accept that every existing article ID becomes stale).

Downstream stages (``gaps``, ``generate``, ``contexts``, ``verify``) all
read the plan's ``md_path`` so no one has to guess where an article lives.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.config.config import Settings
from app.core.errors import KebabError
from app.core.markdown import write_article
from app.pipeline.organize_agent import (
    HierarchyNode,
    HierarchyPlan,
    _slugify,
    propose_hierarchy,
    propose_incremental_hierarchy,
)
from app.models.frontmatter import FrontmatterSchema

logger = logging.getLogger(__name__)


HierarchyProposer = Callable[[Settings, str, list[tuple[str, str]]], HierarchyPlan]
IncrementalProposer = Callable[
    [Settings, HierarchyPlan, list[tuple[str, str]]], HierarchyPlan
]


@dataclass
class OrganizeResult:
    """Summary of an organize run."""

    plan: HierarchyPlan
    plan_path: Path
    created: list[Path]
    existing: list[Path]
    loaded_from_cache: bool
    extended_articles: list[str] = field(default_factory=list)
    added_articles: list[str] = field(default_factory=list)


_MANIFEST_SNIPPET_CHARS = 2000


def plan_path(settings: Settings) -> Path:
    """Return the canonical plan path for this knowledge root."""
    return Path(settings.KNOWLEDGE_DIR) / ".kebab" / "plan.json"


def load_plan(settings: Settings) -> HierarchyPlan | None:
    """Load the canonical plan if it exists, else ``None``."""
    path = plan_path(settings)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise KebabError(f"invalid plan.json at {path}: {exc}") from exc
    return HierarchyPlan.model_validate(raw)


def _build_manifest(settings: Settings) -> list[tuple[str, str]]:
    """Build ``[(label, snippet), …]`` from the processed/ tree.

    Labels include source index IDs so the LLM can reference them
    in source_files as integers. Sources not in the index are skipped
    when the index exists; if there is no index at all, all processed
    docs are included with their stem as the label (backward compat).
    """
    from app.core.sources.index import load_index

    index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    index = load_index(index_path)
    has_index = len(index.sources) > 0

    processed_docs = Path(settings.PROCESSED_DIR) / "documents"
    manifest: list[tuple[str, str]] = []
    if processed_docs.exists():
        for sub in sorted(processed_docs.iterdir()):
            text_path = sub / "text.md"
            if not text_path.exists():
                continue
            snippet = text_path.read_text(encoding="utf-8")[:_MANIFEST_SNIPPET_CHARS]
            entry = index.get_by_stem(sub.name)
            if entry is not None:
                label = f"[{entry.id}] {entry.title}"
            elif has_index:
                logger.debug("organize: skipping %s — not in source index", sub.name)
                continue
            else:
                label = sub.name
            manifest.append((label, snippet))
    # Also scan processed/web/ for web-ingested sources
    processed_web = Path(settings.PROCESSED_DIR) / "web"
    if processed_web.exists():
        for md_file in sorted(processed_web.glob("*.md")):
            snippet = md_file.read_text(encoding="utf-8")[:_MANIFEST_SNIPPET_CHARS]
            stem = md_file.stem
            entry = index.get_by_stem(stem)
            if entry is not None:
                label = f"[{entry.id}] {entry.title}"
            elif has_index:
                logger.debug("organize: skipping web source %s — not in source index", stem)
                continue
            else:
                label = stem
            manifest.append((label, snippet))
    return manifest


def _resolve_md_path(
    settings: Settings, plan: HierarchyPlan, node: HierarchyNode
) -> Path | None:
    """Return the canonical markdown path for an ``article`` node.

    The path is derived deterministically from the node's ancestors:
    ``<CURATED_DIR>/<domain_name>/<subdomain_name>/<slug>.md``.
    Non-article nodes return ``None``.
    """
    if node.level_type != "article":
        return None
    parents_by_id = {n.id: n for n in plan.nodes}
    domain: str | None = None
    subdomain: str | None = None
    cursor: HierarchyNode | None = node
    while cursor is not None and cursor.parent_id is not None:
        cursor = parents_by_id.get(cursor.parent_id)
        if cursor is None:
            break
        if cursor.level_type == "subdomain":
            subdomain = cursor.name
        elif cursor.level_type == "domain":
            domain = cursor.name
    if domain is None:
        return None
    base = Path(settings.CURATED_DIR) / domain
    if subdomain:
        base = base / subdomain
    return base / f"{_slugify(node.name)}.md"


def _resolve_all_paths(settings: Settings, plan: HierarchyPlan) -> HierarchyPlan:
    """Return a new plan with ``md_path`` set on every article node."""
    resolved_nodes: list[HierarchyNode] = []
    for node in plan.nodes:
        resolved = node.model_copy()
        if resolved.level_type == "article":
            path = _resolve_md_path(settings, plan, node)
            resolved.md_path = str(path) if path else None
        resolved_nodes.append(resolved)
    return HierarchyPlan(nodes=resolved_nodes)


def _persist_plan(plan: HierarchyPlan, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(plan.model_dump_json(indent=2), encoding="utf-8")


def _covered_ids(plan: HierarchyPlan) -> set[int]:
    """Return the union of every ``source_files`` entry across article nodes.

    Source files are now integer IDs from the source index.
    """
    covered: set[int] = set()
    for node in plan.nodes:
        if node.level_type != "article":
            continue
        for source_id in node.source_files:
            covered.add(source_id)
    return covered


def _extract_manifest_id(label: str) -> int | None:
    """Extract the source ID from a manifest label like ``[3] Title``."""
    import re
    m = re.match(r"^\[(\d+)\]", label)
    return int(m.group(1)) if m else None


def _select_new_manifest(
    plan: HierarchyPlan, manifest: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Return manifest entries whose source ID is not yet covered by ``plan``.

    Manifest labels have the form ``[N] Title`` — entries without an [N]
    prefix (no source index entry) are always included.
    """
    covered = _covered_ids(plan)
    result: list[tuple[str, str]] = []
    for label, snippet in manifest:
        source_id = _extract_manifest_id(label)
        if source_id is None or source_id not in covered:
            result.append((label, snippet))
    return result


def _merge_plans(
    existing: HierarchyPlan, update: HierarchyPlan
) -> tuple[HierarchyPlan, list[str], list[str]]:
    """Merge an incremental ``update`` into ``existing``.

    Returns ``(merged_plan, extended_article_ids, added_article_ids)``.

    Rules:
    - Nodes in ``update`` whose id matches an existing node: union
      ``source_files`` into the existing node. Other fields are left alone
      (we never rename or re-parent an existing node).
    - Nodes in ``update`` whose id does not exist: appended as new nodes.
    - New nodes must reference a ``parent_id`` that exists in either the
      existing plan or the update itself — otherwise they are dropped with
      a warning (the LLM invented a dangling reference).
    """
    by_id: dict[str, HierarchyNode] = {n.id: n.model_copy() for n in existing.nodes}
    extended: list[str] = []
    added: list[str] = []

    update_ids = {n.id for n in update.nodes}
    for node in update.nodes:
        if node.id in by_id:
            target = by_id[node.id]
            if target.level_type != node.level_type:
                logger.warning(
                    "organize: incremental node %s level_type mismatch "
                    "(existing=%s, proposed=%s) — ignoring extension",
                    node.id,
                    target.level_type,
                    node.level_type,
                )
                continue
            merged_sources = list(
                dict.fromkeys([*target.source_files, *node.source_files])
            )
            if merged_sources != target.source_files:
                target.source_files = merged_sources
                if target.level_type == "article":
                    extended.append(target.id)
            continue

        if node.parent_id is not None:
            parent_available = node.parent_id in by_id or node.parent_id in update_ids
            if not parent_available:
                logger.warning(
                    "organize: incremental node %s has dangling parent %s — dropped",
                    node.id,
                    node.parent_id,
                )
                continue
        by_id[node.id] = node.model_copy()
        if node.level_type == "article":
            added.append(node.id)

    # Preserve the existing order and append new nodes in update order.
    merged_nodes: list[HierarchyNode] = []
    seen: set[str] = set()
    for node in existing.nodes:
        merged_nodes.append(by_id[node.id])
        seen.add(node.id)
    for node in update.nodes:
        if node.id in seen:
            continue
        if node.id not in by_id:
            continue  # dropped for dangling parent
        merged_nodes.append(by_id[node.id])
        seen.add(node.id)

    return HierarchyPlan(nodes=merged_nodes), extended, added


def _materialize_stubs(plan: HierarchyPlan) -> tuple[list[Path], list[Path]]:
    """Create empty markdown stubs for every article node.

    Returns ``(created, existing)`` — existing files are never overwritten.
    """
    created: list[Path] = []
    existing: list[Path] = []
    for node in plan.nodes:
        if node.level_type != "article" or not node.md_path:
            continue
        path = Path(node.md_path)
        if path.exists():
            existing.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        fm = FrontmatterSchema(
            id=node.id,
            name=node.name,
            type="article",
            sources=[],
        )
        body = f"# {node.name}\n\n> {node.description}\n\nTODO: write this article.\n"
        write_article(path, fm, body)
        created.append(path)
    return created, existing


def run(
    settings: Settings,
    *,
    domain_hint: str = "Knowledge",
    proposer: HierarchyProposer = propose_hierarchy,
    incremental_proposer: IncrementalProposer = propose_incremental_hierarchy,
    force: bool = False,
) -> OrganizeResult:
    """Execute the organize stage.

    Three code paths:

    1. **No cache or ``force=True``**: call the full ``proposer`` on every
       source and write the plan from scratch.
    2. **Cache + no new sources**: load the cached plan and re-materialize
       any missing stubs. No LLM call.
    3. **Cache + new sources**: call ``incremental_proposer`` with the
       existing plan + new manifest entries, merge the delta into the
       existing plan (preserving all pre-existing IDs), and re-materialize.

    Extended article IDs are returned in ``extended_articles`` so
    downstream stages can detect staleness (e.g. ``gaps`` compares the
    plan's ``source_files`` against each article's frontmatter
    ``source_stems`` to decide whether to regenerate).
    """
    target = plan_path(settings)

    if not force:
        cached = load_plan(settings)
        if cached is not None:
            manifest = _build_manifest(settings)
            new_entries = _select_new_manifest(cached, manifest)
            if not new_entries:
                created, existing = _materialize_stubs(cached)
                logger.info(
                    "organize: loaded cached plan from %s "
                    "(%d nodes, %d missing stubs restored)",
                    target,
                    len(cached.nodes),
                    len(created),
                )
                return OrganizeResult(
                    plan=cached,
                    plan_path=target,
                    created=created,
                    existing=existing,
                    loaded_from_cache=True,
                )

            logger.info(
                "organize: %d new source(s) not yet in plan — running incremental proposer",
                len(new_entries),
            )
            delta = incremental_proposer(settings, cached, new_entries)
            merged, extended, added = _merge_plans(cached, delta)
            resolved = _resolve_all_paths(settings, merged)
            _persist_plan(resolved, target)
            created, existing = _materialize_stubs(resolved)
            logger.info(
                "organize: extended %d article(s), added %d new article(s) → %s",
                len(extended),
                len(added),
                target,
            )
            return OrganizeResult(
                plan=resolved,
                plan_path=target,
                created=created,
                existing=existing,
                loaded_from_cache=True,
                extended_articles=extended,
                added_articles=added,
            )

    manifest = _build_manifest(settings)
    if not manifest:
        logger.warning("organize: no raw documents under %s", settings.RAW_DIR)
        empty = HierarchyPlan(nodes=[])
        _persist_plan(empty, target)
        return OrganizeResult(
            plan=empty,
            plan_path=target,
            created=[],
            existing=[],
            loaded_from_cache=False,
        )

    raw_plan = proposer(settings, domain_hint, manifest)
    resolved = _resolve_all_paths(settings, raw_plan)
    _persist_plan(resolved, target)
    created, existing = _materialize_stubs(resolved)
    logger.info(
        "organize: %d nodes proposed, %d stubs created (%d already existed) → %s",
        len(resolved.nodes),
        len(created),
        len(existing),
        target,
    )
    return OrganizeResult(
        plan=resolved,
        plan_path=target,
        created=created,
        existing=existing,
        loaded_from_cache=False,
    )
