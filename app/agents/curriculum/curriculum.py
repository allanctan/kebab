"""Curriculum ingest orchestrator.

Reads an XLSX from ``knowledge/raw/curriculum/`` (or anywhere on disk),
parses it into a :class:`CurriculumSpine`, and writes the spine YAML to
``knowledge/.kebab/curriculum/<name>.yaml``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.agents.curriculum.parser import parse_xlsx
from app.config.config import Settings
from app.models.curriculum import CurriculumSpine

logger = logging.getLogger(__name__)


class IngestResult(BaseModel):
    """Summary of a single ``kebab curriculum ingest`` run."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Spine name written.")
    spine_path: Path = Field(..., description="Path of the written spine YAML.")
    total_competencies: int = Field(..., description="Competencies in the spine.")
    grade_filter: str | None = Field(default=None, description="Grade filter used, if any.")


@dataclass
class _IngestPaths:
    """Resolved paths for a single ingest run."""

    source_xlsx: Path
    source_relative: str
    output_yaml: Path


def _resolve_paths(
    settings: Settings,
    xlsx_path: Path,
    name: str,
) -> _IngestPaths:
    """Resolve absolute paths + a portable relative path for provenance."""
    abs_xlsx = xlsx_path.resolve()
    knowledge_root = Path(settings.KNOWLEDGE_DIR).resolve()
    try:
        source_relative = str(abs_xlsx.relative_to(knowledge_root))
    except ValueError:
        # Source outside the knowledge tree — fall back to the absolute path.
        source_relative = str(abs_xlsx)

    output_dir = knowledge_root / ".kebab" / "curriculum"
    output_yaml = output_dir / f"{name}.yaml"
    return _IngestPaths(
        source_xlsx=abs_xlsx,
        source_relative=source_relative,
        output_yaml=output_yaml,
    )


def ingest_xlsx(
    settings: Settings,
    *,
    xlsx_path: Path,
    name: str,
    curriculum: str = "MATATAG",
    grade_filter: str | None = None,
) -> IngestResult:
    """Parse ``xlsx_path`` and write a curriculum spine YAML.

    Args:
        settings:     KEBAB runtime configuration.
        xlsx_path:    Path to the XLSX file (does not have to be under knowledge/).
        name:         Spine name (e.g. ``matatag-g10-draft``). Becomes the filename.
        curriculum:   Curriculum framework name (e.g. ``MATATAG``).
        grade_filter: Keep only competencies for this grade (e.g. ``"10"``).
    """
    paths = _resolve_paths(settings, xlsx_path, name)
    competencies = parse_xlsx(paths.source_xlsx, grade_filter=grade_filter)

    spine = CurriculumSpine(
        name=name,
        curriculum=curriculum,
        source_file=paths.source_relative,
        generated_at=date.today(),
        grade_filter=grade_filter,
        competencies=competencies,
    )

    paths.output_yaml.parent.mkdir(parents=True, exist_ok=True)
    paths.output_yaml.write_text(
        yaml.safe_dump(
            spine.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
            width=120,
        ),
        encoding="utf-8",
    )

    logger.info(
        "curriculum ingest: wrote %s (%d competencies)",
        paths.output_yaml,
        len(competencies),
    )
    return IngestResult(
        name=name,
        spine_path=paths.output_yaml,
        total_competencies=len(competencies),
        grade_filter=grade_filter,
    )


def load_spine(settings: Settings, name: str) -> CurriculumSpine:
    """Read a spine YAML by name."""
    path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "curriculum" / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"spine not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return CurriculumSpine.model_validate(data)


__all__ = ["IngestResult", "ingest_xlsx", "load_spine"]
