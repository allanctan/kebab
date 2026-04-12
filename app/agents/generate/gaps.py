"""Stage 3 — gaps: diff the canonical plan against the live KB.

Reads ``knowledge/.kebab/plan.json`` (written by :mod:`app.agents.organize`)
and emits a :class:`GapReport` listing article-level nodes that are not
yet in the Qdrant index. Each gap carries its ``target_path`` so the
generate stage can write to the exact location organize reserved — no
more parallel trees.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config.config import Settings
from app.core.errors import KebabError
from app.core.markdown import read_article
from app.agents.organize.agent import HierarchyNode, HierarchyPlan
from app.core.store import Store
from app.agents.organize import load_plan

logger = logging.getLogger(__name__)

_STUB_MARKER = "TODO: write this article."


def _is_stub(md_path: str | None) -> bool:
    """Return True if the markdown file is an unwritten organize placeholder."""
    if md_path is None:
        return True
    path = Path(md_path)
    if not path.exists():
        return True
    try:
        _fm, body, _ = read_article(path)
    except Exception:  # noqa: BLE001
        return True
    return _STUB_MARKER in body


class Gap(BaseModel):
    """One missing or stale article, pointing at the path organize reserved."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Article ID from the canonical plan.")
    name: str = Field(..., description="Article name.")
    description: str = Field(..., description="One-sentence description.")
    source_files: list[int] = Field(
        default_factory=list,
        description="Source index IDs the generate stage should ground against.",
    )
    target_path: str | None = Field(
        default=None,
        description="Canonical markdown path reserved by organize.",
    )
    reason: Literal["new", "stale"] = Field(
        default="new",
        description="Why this gap exists: 'new' = not indexed, "
        "'stale' = indexed but plan has new source files the article lacks.",
    )


class GapReport(BaseModel):
    """Output of the gaps stage."""

    model_config = ConfigDict(extra="forbid")

    gaps: list[Gap] = Field(..., description="Article nodes absent from the index.")
    existing: list[str] = Field(
        default_factory=list, description="Article IDs already in the index."
    )


@dataclass
class GapResult:
    report: GapReport
    output_path: Path


def _node_to_gap(node: HierarchyNode, *, reason: Literal["new", "stale"] = "new") -> Gap:
    return Gap(
        id=node.id,
        name=node.name,
        description=node.description,
        source_files=list(node.source_files),
        target_path=node.md_path,
        reason=reason,
    )


def _read_source_ids(md_path: str | None) -> set[int] | None:
    """Return the set of source IDs from a curated article's frontmatter."""
    if md_path is None:
        return None
    path = Path(md_path)
    if not path.exists():
        return None
    try:
        fm, _body, _ = read_article(path)
    except Exception:  # noqa: BLE001
        return None
    if not fm.sources:
        return None
    return {s.id for s in fm.sources}


def _is_stale(node: HierarchyNode) -> bool:
    """Return True if the curated article is missing sources the plan has."""
    recorded = _read_source_ids(node.md_path)
    if recorded is None:
        return False
    plan_ids = set(node.source_files)
    return bool(plan_ids - recorded)


def run(
    settings: Settings,
    *,
    domain: str = "default",
    plan: HierarchyPlan | None = None,
    store: Store | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> GapResult:
    """Execute the gaps stage against the plan for ``domain``."""
    plan = plan if plan is not None else load_plan(settings, domain)
    if plan is None:
        raise KebabError(f"gaps: no plan found for domain '{domain}' — run `kebab organize --domain {domain}` first")

    store = store or Store(settings)
    store.ensure_collection()
    indexed_ids = {article.id for article in store.scroll()}

    gaps: list[Gap] = []
    existing: list[str] = []
    for node in plan.nodes:
        if node.level_type != "article":
            continue
        if node.id in indexed_ids:
            if _is_stale(node):
                gaps.append(_node_to_gap(node, reason="stale"))
            else:
                existing.append(node.id)
        elif _is_stub(node.md_path):
            gaps.append(_node_to_gap(node, reason="new"))
        else:
            # Written by generate but not yet synced to Qdrant.
            if _is_stale(node):
                gaps.append(_node_to_gap(node, reason="stale"))
            else:
                existing.append(node.id)

    report = GapReport(gaps=gaps, existing=existing)
    out_dir = Path(settings.KNOWLEDGE_DIR) / ".kebab"
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = out_dir / f"gaps-{timestamp}.json"
    out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info(
        "gaps: %d gap(s), %d already indexed → %s",
        len(gaps),
        len(existing),
        out_path,
    )
    return GapResult(report=report, output_path=out_path)


def latest_gaps(settings: Settings) -> GapReport | None:
    out_dir = Path(settings.KNOWLEDGE_DIR) / ".kebab"
    if not out_dir.exists():
        return None
    files = sorted(out_dir.glob("gaps-*.json"))
    if not files:
        return None
    raw = json.loads(files[-1].read_text(encoding="utf-8"))
    return GapReport.model_validate(raw)
