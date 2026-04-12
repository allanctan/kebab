"""Sync package — parse curated markdown and upsert into Qdrant.

Also provides :func:`auto_sync` for other stages to trigger a sync
after modifying articles (e.g. after generate or research).
"""

from __future__ import annotations

import logging

from app.agents.sync.sync import SyncResult as SyncResult
from app.agents.sync.sync import run as run
from app.config.config import Settings

logger = logging.getLogger(__name__)


def auto_sync(settings: Settings, caller: str) -> None:
    """Sync curated articles to Qdrant. Swallows errors gracefully.

    Called automatically by generate and research stages after modifying
    articles so the Qdrant index stays current without a manual ``kebab sync``.
    """
    try:
        result = run(settings)
        logger.info("%s: auto-synced %d article(s) to Qdrant", caller, result.articles)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: auto-sync failed — run `kebab sync` manually: %s", caller, exc)
