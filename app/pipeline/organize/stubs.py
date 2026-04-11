"""Stub materialization — resolves md_path for nodes and creates empty stubs."""

from __future__ import annotations

from pathlib import Path

from app.config.config import Settings
from app.core.markdown import write_article
from app.models.frontmatter import FrontmatterSchema
from app.pipeline.organize.agent import HierarchyNode, HierarchyPlan, _slugify


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
