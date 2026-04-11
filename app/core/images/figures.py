"""Figure manifest loading, marker validation, and file copying.

Supports the generate stage's figure placement workflow:
1. Load available figures from ``figures.json``
2. Build a numbered manifest for the LLM
3. Validate ``[FIGURE:N]`` markers against the manifest
4. Copy used figures to the article's figures directory
5. Resolve markers to ``![description](path)`` markdown
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_FIGURE_MARKER_RE = re.compile(r"\[FIGURE:(\d+)\]")


@dataclass
class FigureEntry:
    """One available figure in the manifest."""

    local_num: int
    figure_id: str
    description: str
    source_path: Path
    mime_type: str


@dataclass
class FigureManifest:
    """Numbered manifest of available figures for one article."""

    entries: list[FigureEntry] = field(default_factory=list)

    def get(self, local_num: int) -> FigureEntry | None:
        for entry in self.entries:
            if entry.local_num == local_num:
                return entry
        return None

    def prompt_text(self) -> str:
        """Build the manifest text for the LLM prompt."""
        if not self.entries:
            return ""
        lines = ["Available figures:"]
        for e in self.entries:
            lines.append(f"[{e.local_num}] {e.figure_id} — \"{e.description}\"")
        return "\n".join(lines)


def load_figure_manifest(processed_dir: Path) -> FigureManifest:
    """Load useful figures from a processed document directory.

    Skips decorative, filtered, and error figures. Returns a numbered
    manifest ready for the LLM prompt.
    """
    figures_json = processed_dir / "figures.json"
    if not figures_json.exists():
        return FigureManifest()

    raw = json.loads(figures_json.read_text(encoding="utf-8"))
    entries: list[FigureEntry] = []
    num = 1
    for record in raw:
        path = record.get("path", "")
        description = record.get("description", "")
        skip = record.get("skip_reason", "")

        if not path or not description:
            continue
        if description == "DECORATIVE" or description.startswith("ERROR:"):
            continue
        if skip:
            continue

        source_path = processed_dir / path
        if not source_path.exists():
            logger.debug("figures: %s referenced but missing on disk", path)
            continue

        figure_id = Path(path).stem
        entries.append(FigureEntry(
            local_num=num,
            figure_id=figure_id,
            description=description,
            source_path=source_path,
            mime_type=record.get("mime_type", "image/jpeg"),
        ))
        num += 1

    return FigureManifest(entries=entries)


def resolve_figure_markers(
    body: str,
    manifest: FigureManifest,
    article_slug: str,
) -> tuple[str, list[FigureEntry]]:
    """Replace ``[FIGURE:N]`` markers with image markdown.

    Returns ``(resolved_body, used_entries)``. Invalid markers (N not
    in manifest) are stripped with a warning.
    """
    used: list[FigureEntry] = []

    def _replace(match: re.Match[str]) -> str:
        num = int(match.group(1))
        entry = manifest.get(num)
        if entry is None:
            logger.warning("figures: [FIGURE:%d] not in manifest — stripping", num)
            return ""
        used.append(entry)
        ext = Path(entry.source_path).suffix
        rel_path = f"figures/{article_slug}/{entry.figure_id}{ext}"
        return f"![{entry.description}]({rel_path})"

    resolved = _FIGURE_MARKER_RE.sub(_replace, body)
    return resolved, used


def copy_figures(entries: list[FigureEntry], dest_dir: Path) -> None:
    """Copy figure files to the article's figures directory."""
    if not entries:
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        ext = entry.source_path.suffix
        target = dest_dir / f"{entry.figure_id}{ext}"
        if not entry.source_path.exists():
            logger.warning("figures: source %s not found — skipping copy", entry.source_path)
            continue
        shutil.copy2(entry.source_path, target)
