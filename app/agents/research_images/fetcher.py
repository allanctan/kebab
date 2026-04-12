"""Download Wikipedia images and apply skip-keyword prefilters.

No LLM calls. Wraps :func:`app.agents.ingest.adapters.wikipedia.fetch_article_images`
for the API call and adds the local-disk download + skip-keyword filtering
that today's ``research/agent.py`` does inline.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.agents.ingest.adapters.wikipedia import fetch_article_images
from app.config.config import Settings
from app.core.sources.fetcher import user_agent

logger = logging.getLogger(__name__)


@dataclass
class ImageCandidate:
    """An image considered for inclusion in the article."""

    local_path: Path
    source_title: str       # Wikipedia article title this came from
    raw_description: str    # Description from the Wikipedia API
    llm_description: str = ""  # Filled in by the describer step


@dataclass
class _SkipCache:
    """Lazy cache for the per-knowledge-dir skip keyword file."""

    keywords: list[str] = field(default_factory=list)
    loaded_for: Path | None = None


_skip_cache = _SkipCache()


def load_skip_keywords(settings: Settings) -> list[str]:
    """Read ``.kebab/image_skip_keywords.txt`` and return a list of keywords.

    Returns an empty list if the file doesn't exist. Comments (lines
    starting with ``#``) and blank lines are skipped. Cached per
    knowledge-dir so repeated calls within one CLI invocation are free.
    """
    knowledge = Path(settings.KNOWLEDGE_DIR)
    if _skip_cache.loaded_for == knowledge:
        return _skip_cache.keywords

    skip_file = knowledge / ".kebab" / "image_skip_keywords.txt"
    keywords: list[str] = []
    if skip_file.exists():
        keywords = [
            line.strip().lower()
            for line in skip_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    _skip_cache.keywords = keywords
    _skip_cache.loaded_for = knowledge
    return keywords


def fetch_wikipedia_images(wiki_title: str, *, limit: int = 3) -> list[dict[str, str]]:
    """Wrap the wiki adapter's :func:`fetch_article_images` for the agent."""
    try:
        return fetch_article_images(wiki_title, limit=limit)
    except Exception as exc:
        logger.warning(
            "research-images: fetch_article_images failed for %r: %s",
            wiki_title,
            exc,
        )
        return []


def is_decorative_by_keyword(image: dict[str, str], skip_keywords: list[str]) -> bool:
    """Return True if the image's Wikipedia description matches any skip keyword."""
    desc = image.get("description", "").lower()
    return any(keyword in desc for keyword in skip_keywords)


def download(image: dict[str, str], *, dest: Path) -> Path | None:
    """Download an image to ``dest`` and return the local path, or None on failure.

    The filename is derived from the image description (slugified) plus the
    URL extension. ``dest`` is created if it does not exist.
    """
    image_url = image.get("url", "")
    if not image_url:
        return None

    try:
        response = httpx.get(
            image_url,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": user_agent()},
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("research-images: download failed for %s: %s", image_url, exc)
        return None

    description = image.get("description", "")
    ext = Path(image_url.split("?")[0]).suffix or ".png"
    slug = re.sub(r"[^a-z0-9]+", "-", description.lower().strip())[:40].strip("-") or "image"
    filename = f"wiki-{slug}{ext}"

    dest.mkdir(parents=True, exist_ok=True)
    target = dest / filename
    target.write_bytes(response.content)
    return target


__all__ = [
    "ImageCandidate",
    "download",
    "fetch_wikipedia_images",
    "is_decorative_by_keyword",
    "load_skip_keywords",
]
