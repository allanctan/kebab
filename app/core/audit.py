"""Per-article audit logging — structured JSONL sidecar files.

Each curated article gets a ``<stem>.audit.jsonl`` file next to the
markdown. Every pipeline stage appends structured events when it modifies
the article: confirms, appends, disputes, gap answers, image additions,
context classifications, etc.

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


def _audit_path(article_path: Path) -> Path:
    """Return the audit log path for an article: ``<stem>.audit.jsonl``."""
    return article_path.with_suffix(".audit.jsonl")


def log_event(
    article_path: Path,
    *,
    stage: str,
    action: str,
    detail: str,
    article_id: str = "",
) -> None:
    """Append one audit event to the article's sidecar log.

    Args:
        article_path: Path to the curated ``.md`` file.
        stage: Pipeline stage name (``research``, ``research-gaps``, etc.).
        action: What happened (``confirm``, ``append``, ``dispute``, etc.).
        detail: Human-readable description.
        article_id: Article ID for cross-referencing (optional — can be
            derived from the path, but including it makes grepping easier).
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
        with audit_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.debug("audit: failed to write to %s: %s", audit_file, exc)


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
