"""Plan persistence — domain slug, plan path, load/list helpers."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.config.config import Settings
from app.core.errors import KebabError
from app.pipeline.organize.agent import HierarchyPlan

logger = logging.getLogger(__name__)


def _domain_slug(domain: str) -> str:
    """Convert a domain hint to a filesystem-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-") or "default"


def plan_path(settings: Settings, domain: str = "default") -> Path:
    """Return the plan path for a domain."""
    slug = _domain_slug(domain)
    return Path(settings.KNOWLEDGE_DIR) / ".kebab" / f"plan-{slug}.json"


def load_plan(settings: Settings, domain: str = "default") -> HierarchyPlan | None:
    """Load the plan for a domain if it exists, else ``None``."""
    path = plan_path(settings, domain)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise KebabError(f"invalid plan at {path}: {exc}") from exc
    return HierarchyPlan.model_validate(raw)


def list_domains(settings: Settings) -> list[str]:
    """Return all domain slugs that have a plan file."""
    kebab_dir = Path(settings.KNOWLEDGE_DIR) / ".kebab"
    if not kebab_dir.exists():
        return []
    return sorted(
        p.stem.removeprefix("plan-")
        for p in kebab_dir.glob("plan-*.json")
    )


def _persist_plan(plan: HierarchyPlan, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
