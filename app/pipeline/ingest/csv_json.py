"""Ingest CSV/JSON datasets into ``knowledge/raw/datasets/``.

JSON files are parsed before being copied so we fail loudly on invalid
input rather than dragging it through the pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config.config import Settings
from app.core.errors import IngestError

logger = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    """Return hex SHA256 digest of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class DatasetIngestResult:
    """Output of a single dataset ingest call."""

    target: Path
    kind: str  # "csv" | "json"


def ingest(settings: Settings, input_path: Path) -> DatasetIngestResult:
    """Copy ``input_path`` into ``raw/datasets/`` after light validation."""
    if not input_path.exists() or not input_path.is_file():
        raise IngestError(f"dataset not found: {input_path}")
    suffix = input_path.suffix.lower()
    if suffix not in {".csv", ".json"}:
        raise IngestError(f"unsupported dataset extension: {suffix}")

    if suffix == ".json":
        try:
            json.loads(input_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise IngestError(f"invalid JSON in {input_path}: {exc}") from exc

    raw_dir = Path(settings.RAW_DIR) / "datasets"
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / input_path.name
    shutil.copy2(input_path, target)
    logger.info("ingested dataset %s → %s", input_path.name, target)
    from app.core.sources.index import load_index, register_source, save_index

    index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    index = load_index(index_path)
    knowledge_root = Path(settings.KNOWLEDGE_DIR)
    register_source(
        index,
        stem=target.stem.replace(" ", "_"),
        raw_path=str(target.relative_to(knowledge_root)),
        title=input_path.stem.replace("_", " "),
        tier=3,
        checksum=_sha256(target),
        adapter="local_dataset",
        path_pattern=getattr(settings, "SOURCE_PATH_PATTERN", None),
    )
    save_index(index, index_path)
    return DatasetIngestResult(target=target, kind=suffix.lstrip("."))


def ingest_tree(settings: Settings, root: Path) -> list[DatasetIngestResult]:
    """Recursively ingest every ``*.csv`` / ``*.json`` under ``root``."""
    if not root.exists() or not root.is_dir():
        raise IngestError(f"not a directory: {root}")
    raw_dir = (Path(settings.RAW_DIR) / "datasets").resolve()
    results: list[DatasetIngestResult] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".csv", ".json"}:
            continue
        # Skip files already flat under raw/datasets/.
        if path.resolve().parent == raw_dir:
            continue
        results.append(ingest(settings, path))
    logger.info("ingested %d dataset(s) from %s", len(results), root)
    return results
