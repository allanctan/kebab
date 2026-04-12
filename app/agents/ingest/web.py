"""Ingest web pages into ``knowledge/raw/documents/``.

Uses Jina Reader API (https://r.jina.ai/) for clean markdown extraction.
Falls back to BeautifulSoup if Jina is unavailable.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config.config import Settings

logger = logging.getLogger(__name__)

_JINA_PREFIX = "https://r.jina.ai/"


@dataclass
class WebIngestResult:
    """Output of a single web ingest call."""

    raw_path: Path
    text_path: Path
    chars: int


def _slug(url: str) -> str:
    """Build a filesystem-safe slug from a URL."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", url).strip("-").lower()
    short = cleaned[:64] if cleaned else "page"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{short}-{digest}"


def _fetch_jina(url: str) -> tuple[str, str]:
    """Fetch clean markdown via Jina Reader API.

    Returns (raw_response, clean_markdown).
    """
    from app.core.sources.fetcher import user_agent
    jina_url = f"{_JINA_PREFIX}{url}"
    response = httpx.get(
        jina_url,
        timeout=30.0,
        headers={
            "Accept": "text/markdown",
            "User-Agent": user_agent(),
        },
        follow_redirects=True,
    )
    response.raise_for_status()
    text = response.text
    return text, text


def ingest(settings: Settings, url: str) -> WebIngestResult:
    """Fetch ``url`` via Jina Reader, save markdown to ``raw/web/`` and ``processed/web/``."""
    raw_dir = Path(settings.RAW_DIR) / "web"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir = Path(settings.PROCESSED_DIR) / "web"
    processed_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(url)
    raw_path = raw_dir / f"{slug}.md"
    text_path = processed_dir / f"{slug}.md"

    raw, text = _fetch_jina(url)
    logger.info("ingested %s via Jina Reader (%d chars)", url, len(text))

    # Extract page title from Jina markdown (first line: "Title: ...")
    title = url
    for line in text.splitlines():
        if line.startswith("Title:"):
            title = line.removeprefix("Title:").strip()
            break

    raw_path.write_text(raw, encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")

    from app.core.sources.index import load_index, register_source, save_index

    index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    index = load_index(index_path)
    knowledge_root = Path(settings.KNOWLEDGE_DIR)
    register_source(
        index,
        stem=slug,
        raw_path=str(raw_path.relative_to(knowledge_root)),
        title=title,
        tier=4,
        checksum=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        adapter="direct_url",
        path_pattern=getattr(settings, "SOURCE_PATH_PATTERN", None),
    )
    save_index(index, index_path)
    return WebIngestResult(raw_path=raw_path, text_path=text_path, chars=len(text))
