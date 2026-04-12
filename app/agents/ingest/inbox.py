"""Staging helpers for ``raw/inbox/``.

External sources found by the research agent are staged here before
being promoted to ``raw/documents/``. Each file gets a provenance
sidecar via the standard ``write_sidecar`` path.
"""

from __future__ import annotations

from pathlib import Path


def inbox_path(knowledge_dir: Path) -> Path:
    """Return the inbox directory path."""
    return knowledge_dir / "raw" / "inbox"


def stage_to_inbox(knowledge_dir: Path, filename: str, content: bytes) -> Path:
    """Write ``content`` to ``raw/inbox/<filename>``. Returns the path."""
    target = inbox_path(knowledge_dir) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def list_inbox(knowledge_dir: Path) -> list[Path]:
    """Return all files in the inbox, sorted by name."""
    inbox = inbox_path(knowledge_dir)
    if not inbox.exists():
        return []
    return sorted(p for p in inbox.iterdir() if p.is_file() and not p.name.endswith(".meta.json"))
