"""Organize package — proposes canonical hierarchy from source documents."""

from __future__ import annotations

from app.pipeline.organize.agent import (
    HierarchyNode as HierarchyNode,
    HierarchyPlan as HierarchyPlan,
)
from app.pipeline.organize.manifest import (
    _covered_ids as _covered_ids,
    _select_new_manifest as _select_new_manifest,
)
from app.pipeline.organize.merge import (
    _merge_plans as _merge_plans,
)
from app.pipeline.organize.models import (
    OrganizeResult as OrganizeResult,
)
from app.pipeline.organize.plan import (
    list_domains as list_domains,
    load_plan as load_plan,
    plan_path as plan_path,
)
from app.pipeline.organize.organize import (
    run as run,
)
