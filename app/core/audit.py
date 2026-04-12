"""Per-article audit logging — JSONL files under ``logs/``.

Each curated article gets a ``logs/<stem>.audit.jsonl`` file. Every
pipeline stage appends structured events when it modifies the article.

Usage::

    from app.core.audit import log_event

    log_event(
        article_path=path,
        stage="research",
        action="confirm",
        detail="Claim 'Plates move...' confirmed via Wikipedia: Plate tectonics",
    )

The log is append-only. Each line is a self-contained JSON object.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Resolved lazily on first call. Tests can monkeypatch this.
_logs_dir: Path | None = None


def _get_logs_dir() -> Path:
    global _logs_dir  # noqa: PLW0603
    if _logs_dir is None:
        from app.config.logging import LOGS_DIR
        _logs_dir = LOGS_DIR
    return _logs_dir


def _audit_path(article_path: Path) -> Path:
    """Return ``logs/<stem>.audit.jsonl``."""
    return _get_logs_dir() / f"{article_path.stem}.audit.jsonl"


def log_event(
    article_path: Path,
    *,
    stage: str,
    action: str,
    detail: str,
    article_id: str = "",
) -> None:
    """Append one audit event to ``logs/<article_stem>.audit.jsonl``.

    Args:
        article_path: Path to the curated ``.md`` file.
        stage: Pipeline stage name (``research``, ``research-gaps``, etc.).
        action: What happened (``confirm``, ``append``, ``dispute``, etc.).
        detail: Human-readable description.
        article_id: Article ID for cross-referencing.
    """
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "stage": stage,
        "action": action,
        "detail": detail,
    }
    if article_id:
        entry["article_id"] = article_id

    audit_file = _audit_path(article_path)
    try:
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        with audit_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.debug("audit: failed to write %s: %s", audit_file, exc)


def read_log(article_path: Path) -> list[dict[str, str]]:
    """Read all audit events for an article. Returns empty list if no log exists."""
    audit_file = _audit_path(article_path)
    if not audit_file.exists():
        return []
    entries: list[dict[str, str]] = []
    for line in audit_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


__all__ = ["log_event", "read_log"]
