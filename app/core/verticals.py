from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.config.config import Settings
from app.models.frontmatter import FrontmatterSchema

logger = logging.getLogger(__name__)


@dataclass
class VerticalConfig:
    """Loaded from .kebab/<vertical>.yaml."""

    key: str
    description: str = ""
    generate_instruction: str = ""
    authoritative_sources: list[str] = field(default_factory=list)
    classification_fields: dict[str, Any] = field(default_factory=dict)


def load_verticals(settings: Settings) -> dict[str, VerticalConfig]:
    """Load all .kebab/<name>.yaml files as vertical configs."""
    kebab_dir = Path(settings.KNOWLEDGE_DIR) / ".kebab"
    verticals: dict[str, VerticalConfig] = {}
    if not kebab_dir.exists():
        return verticals
    for yaml_path in sorted(kebab_dir.glob("*.yaml")):
        key = yaml_path.stem
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("verticals: failed to load %s: %s", yaml_path, exc)
            continue
        verticals[key] = VerticalConfig(
            key=key,
            description=data.get("description", ""),
            generate_instruction=data.get("generate_instruction", ""),
            authoritative_sources=data.get("authoritative_sources", []),
            classification_fields=data.get("classification_fields", {}),
        )
    return verticals


def resolve_vertical(
    settings: Settings, fm: FrontmatterSchema
) -> VerticalConfig | None:
    """Find the vertical config matching an article's contexts key."""
    data = fm.model_dump()
    contexts = data.get("contexts")
    if not contexts or not isinstance(contexts, dict):
        return None
    verticals = load_verticals(settings)
    for key in contexts:
        if key in verticals:
            return verticals[key]
    return None
