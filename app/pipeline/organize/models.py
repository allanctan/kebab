"""OrganizeResult dataclass — summary of an organize run."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.pipeline.organize.agent import HierarchyPlan


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
