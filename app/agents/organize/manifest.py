"""Manifest builder — constructs source manifest and selects uncovered entries."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.config.config import Settings
from app.agents.organize.agent import HierarchyPlan

logger = logging.getLogger(__name__)

_MANIFEST_SNIPPET_CHARS = 2000


def _build_manifest(settings: Settings) -> list[tuple[str, str]]:
    """Build ``[(label, snippet), …]`` from the processed/ tree.

    Labels include source index IDs so the LLM can reference them
    in ``source_files`` as integers. Sources not in the index are
    skipped — the ingest step must run first to register them.
    """
    from app.core.sources.index import load_index

    index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    index = load_index(index_path)

    processed_docs = Path(settings.PROCESSED_DIR) / "documents"
    manifest: list[tuple[str, str]] = []
    if processed_docs.exists():
        for sub in sorted(processed_docs.iterdir()):
            text_path = sub / "text.md"
            if not text_path.exists():
                continue
            entry = index.get_by_stem(sub.name)
            if entry is None:
                logger.debug("organize: skipping %s — not in source index", sub.name)
                continue
            snippet = text_path.read_text(encoding="utf-8")[:_MANIFEST_SNIPPET_CHARS]
            label = f"[{entry.id}] {entry.title}"
            manifest.append((label, snippet))
    # Also scan processed/web/ for web-ingested sources
    processed_web = Path(settings.PROCESSED_DIR) / "web"
    if processed_web.exists():
        for md_file in sorted(processed_web.glob("*.md")):
            stem = md_file.stem
            entry = index.get_by_stem(stem)
            if entry is None:
                logger.debug("organize: skipping web source %s — not in source index", stem)
                continue
            snippet = md_file.read_text(encoding="utf-8")[:_MANIFEST_SNIPPET_CHARS]
            label = f"[{entry.id}] {entry.title}"
            manifest.append((label, snippet))
    return manifest


def _covered_ids(plan: HierarchyPlan) -> set[int]:
    """Return the union of every ``source_files`` entry across article nodes.

    Source files are now integer IDs from the source index.
    """
    covered: set[int] = set()
    for node in plan.nodes:
        if node.level_type != "article":
            continue
        for source_id in node.source_files:
            covered.add(source_id)
    return covered


def _extract_manifest_id(label: str) -> int | None:
    """Extract the source ID from a manifest label like ``[3] Title``."""
    m = re.match(r"^\[(\d+)\]", label)
    return int(m.group(1)) if m else None


def _select_new_manifest(
    plan: HierarchyPlan, manifest: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Return manifest entries whose source ID is not yet covered by ``plan``.

    Manifest labels have the form ``[N] Title`` — entries without an [N]
    prefix (no source index entry) are always included.
    """
    covered = _covered_ids(plan)
    result: list[tuple[str, str]] = []
    for label, snippet in manifest:
        source_id = _extract_manifest_id(label)
        if source_id is None or source_id not in covered:
            result.append((label, snippet))
    return result
