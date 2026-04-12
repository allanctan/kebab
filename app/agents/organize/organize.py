"""Organize stage orchestrator — the run() entry point."""

from __future__ import annotations

import logging
from typing import Callable

from app.config.config import Settings
from app.agents.organize.agent import (
    HierarchyPlan,
    propose_hierarchy,
    propose_incremental_hierarchy,
)
from app.agents.organize.manifest import _build_manifest, _select_new_manifest
from app.agents.organize.merge import _merge_plans
from app.agents.organize.models import OrganizeResult
from app.agents.organize.plan import _persist_plan, load_plan, plan_path
from app.agents.organize.stubs import _materialize_stubs, _resolve_all_paths

logger = logging.getLogger(__name__)

HierarchyProposer = Callable[[Settings, str, list[tuple[str, str]]], HierarchyPlan]
IncrementalProposer = Callable[
    [Settings, HierarchyPlan, list[tuple[str, str]]], HierarchyPlan
]


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
    target = plan_path(settings, domain_hint)

    if not force:
        cached = load_plan(settings, domain_hint)
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
