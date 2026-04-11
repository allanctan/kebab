"""Ingest web pages into ``knowledge/raw/documents/``.

Cache-first: the raw HTML is always written to disk before extraction so
we never need to re-fetch the same URL twice.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.config.config import Settings
from app.utils.web_scraper import fetch

logger = logging.getLogger(__name__)


@dataclass
class WebIngestResult:
    """Output of a single web ingest call."""

    html_path: Path
    text_path: Path
    chars: int


def _slug(url: str) -> str:
    """Build a filesystem-safe slug from a URL.

    Combines a sanitized prefix with a short hash so two URLs that
    sanitize to the same prefix don't collide.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", url).strip("-").lower()
    short = cleaned[:64] if cleaned else "page"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{short}-{digest}"


def ingest(settings: Settings, url: str) -> WebIngestResult:
    """Fetch ``url`` and write both raw HTML and cleaned text under raw/documents/."""
    raw_dir = Path(settings.RAW_DIR) / "documents"
    raw_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(url)
    html_path = raw_dir / f"{slug}.html"
    text_path = raw_dir / f"{slug}.txt"

    html, text = fetch(url)
    html_path.write_text(html, encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")
    logger.info("ingested %s → %s (%d chars)", url, text_path, len(text))
    from app.core.sources.index import load_index, register_source, save_index

    index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    index = load_index(index_path)
    knowledge_root = Path(settings.KNOWLEDGE_DIR)
    register_source(
        index,
        stem=slug,
        raw_path=str(text_path.relative_to(knowledge_root)),
        title=url,
        tier=4,
        checksum=hashlib.sha256(html_path.read_bytes()).hexdigest(),
        adapter="direct_url",
        path_pattern=getattr(settings, "SOURCE_PATH_PATTERN", None),
    )
    save_index(index, index_path)
    return WebIngestResult(html_path=html_path, text_path=text_path, chars=len(text))
