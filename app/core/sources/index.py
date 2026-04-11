"""Source index — deterministic registry of all ingested source documents.

Built incrementally at ingest time. Downstream stages (organize, gaps,
generate) reference sources by integer ID rather than filename stems.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class SourceEntry(BaseModel):
    """One registered source in the index."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="Sequential source ID.")
    stem: str = Field(..., description="Underscored filename stem (dedup key).")
    raw_path: str = Field(..., description="Path to raw file, relative to knowledge/.")
    title: str = Field(..., description="Human-readable title.")
    tier: int = Field(..., description="Publisher authority tier (1-5).")
    checksum: str = Field(..., description="SHA256 hex digest of raw bytes.")
    adapter: str = Field(..., description="Name of the adapter that fetched this source.")
    retrieved_at: datetime | None = Field(default=None, description="When the source was fetched.")
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Key-value metadata extracted from the source path pattern.",
    )


class SourceIndex(BaseModel):
    """The full source index, persisted to sources.json."""

    model_config = ConfigDict(extra="forbid")

    sources: list[SourceEntry] = Field(default_factory=list)
    next_id: int = Field(default=1)

    def get(self, source_id: int) -> SourceEntry:
        """Return entry by ID or raise KeyError."""
        for entry in self.sources:
            if entry.id == source_id:
                return entry
        raise KeyError(f"no source with id {source_id}")

    def get_by_stem(self, stem: str) -> SourceEntry | None:
        """Return entry by stem or None."""
        for entry in self.sources:
            if entry.stem == stem:
                return entry
        return None


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a path pattern with ``{field}`` placeholders to a regex.

    Example: ``raw/documents/grade_{grade}/{subject}/{filename}``
    becomes a regex that captures named groups ``grade``, ``subject``,
    ``filename``.
    """
    # Escape everything except {field} placeholders.
    parts: list[str] = []
    last = 0
    for match in re.finditer(r"\{(\w+)\}", pattern):
        parts.append(re.escape(pattern[last : match.start()]))
        parts.append(f"(?P<{match.group(1)}>[^/]+)")
        last = match.end()
    parts.append(re.escape(pattern[last:]))
    return re.compile("^" + "".join(parts) + "$")


def extract_path_metadata(raw_path: str, pattern: str | None) -> dict[str, str]:
    """Extract metadata from ``raw_path`` using a ``{field}`` pattern.

    Returns an empty dict if the pattern is None or doesn't match.
    """
    if not pattern:
        return {}
    regex = _pattern_to_regex(pattern)
    # Normalize separators for matching.
    normalized = raw_path.replace("\\", "/")
    match = regex.match(normalized)
    if not match:
        return {}
    return {k: v for k, v in match.groupdict().items() if k != "filename"}


def load_index(path: Path) -> SourceIndex:
    """Load the source index from disk, or return an empty one."""
    if not path.exists():
        return SourceIndex()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return SourceIndex.model_validate(raw)


def save_index(index: SourceIndex, path: Path) -> None:
    """Persist the source index to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(index.model_dump_json(indent=2), encoding="utf-8")


def register_source(
    index: SourceIndex,
    *,
    stem: str,
    raw_path: str,
    title: str,
    tier: int,
    checksum: str,
    adapter: str,
    retrieved_at: datetime | None = None,
    path_pattern: str | None = None,
) -> SourceEntry:
    """Register or update a source in the index. Returns the entry.

    If ``path_pattern`` is provided (e.g.
    ``raw/documents/grade_{grade}/{subject}/{filename}``), metadata is
    extracted from ``raw_path`` and stored on the entry.
    """
    metadata = extract_path_metadata(raw_path, path_pattern)

    existing = index.get_by_stem(stem)
    if existing is not None:
        existing.raw_path = raw_path
        existing.title = title
        existing.tier = tier
        existing.checksum = checksum
        existing.adapter = adapter
        existing.retrieved_at = retrieved_at
        existing.metadata = metadata
        return existing

    entry = SourceEntry(
        id=index.next_id,
        stem=stem,
        raw_path=raw_path,
        title=title,
        tier=tier,
        checksum=checksum,
        adapter=adapter,
        retrieved_at=retrieved_at,
        metadata=metadata,
    )
    index.sources.append(entry)
    index.next_id += 1
    return entry
