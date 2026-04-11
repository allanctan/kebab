"""Incremental merge — merges an LLM delta plan into the existing plan."""

from __future__ import annotations

import logging

from app.pipeline.organize.agent import HierarchyNode, HierarchyPlan

logger = logging.getLogger(__name__)


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
