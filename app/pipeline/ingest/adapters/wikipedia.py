"""Wikipedia adapter — uses the MediaWiki REST API for discovery and fetch.

``discover(query)`` calls the MediaWiki opensearch API and returns a ranked
list of :class:`Candidate` references. ``fetch(candidate)`` retrieves the
article's plaintext via the MediaWiki extracts API and writes it under
``raw/documents/wikipedia_<slug>.md``.

No API key required — Wikipedia is free.

Why Wikipedia at tier 4? Wikipedia articles come from an open, reputable
platform with broad coverage, but editorial authority varies by article.
Curators can upgrade tier after reviewing the sources cited in the article.

License is always ``CC-BY-SA-3.0``.

Pattern adapted from better-ed-ai/app/agents/assignment/assignment_checker.py
and the KEBAB tavily adapter.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, cast
from urllib.parse import quote

import httpx

from app.config.config import Settings
from app.core.sources.provenance import sha256_bytes, write_sidecar
from app.core.sources.adapter import (
    AdapterError,
    Candidate,
    FetchedArtifact,
    SourceTier,
)
from app.models.source import Source

logger = logging.getLogger(__name__)

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_OPENSEARCH_URL = (
    "https://en.wikipedia.org/w/api.php"
    "?action=opensearch&search={query}&limit={limit}&format=json"
)
_EXTRACTS_URL = (
    "https://en.wikipedia.org/w/api.php"
    "?action=query&prop=extracts&titles={title}"
    "&format=json&explaintext=true&exintro=false&redirects=true"
)
_WIKI_ARTICLE_URL = "https://en.wikipedia.org/wiki/{title}"
_IMAGES_URL = (
    "https://en.wikipedia.org/w/api.php"
    "?action=query&prop=images&titles={title}"
    "&format=json&imlimit=50"
)
_IMAGEINFO_URL = (
    "https://en.wikipedia.org/w/api.php"
    "?action=query&titles={file_title}"
    "&prop=imageinfo&iiprop=url|extmetadata"
    "&format=json"
)

_SKIP_IMAGE_PREFIXES = (
    "File:Wiki",
    "File:Commons",
    "File:Symbol",
    "File:Icon",
    "File:Flag",
    "File:Ambox",
    "File:Question",
    "File:Text-x",
)
_LICENSE = "CC-BY-SA-3.0"


def _title_to_slug(title: str, *, max_len: int = 60) -> str:
    """Derive a safe filesystem slug from a Wikipedia article title.

    Lowercases, replaces runs of non-alphanumeric characters with hyphens,
    and truncates to ``max_len`` characters.
    """
    slug = _SLUG_NON_ALNUM.sub("-", title.lower()).strip("-")
    return slug[:max_len]


@dataclass
class WikipediaAdapter:
    """Wikipedia source adapter. Default tier 4 (reputable platform)."""

    settings: Settings
    name: ClassVar[str] = "wikipedia"
    default_tier: SourceTier = field(default=4)
    _client: httpx.Client | None = field(default=None, repr=False)

    def _get_client(self) -> httpx.Client:
        """Direct httpx client for MediaWiki API calls.

        The MediaWiki API is designed for programmatic access and should
        not be gated by robots.txt checks (which block the opensearch
        endpoint). Uses a polite User-Agent per Wikipedia's API etiquette.
        """
        if self._client is None:
            from app.core.sources.fetcher import user_agent
            self._client = httpx.Client(
                timeout=30.0,
                follow_redirects=True,
                headers={"User-Agent": user_agent(self.settings)},
            )
        return self._client

    def discover(self, query: str, *, limit: int = 10) -> list[Candidate]:
        """Search Wikipedia via opensearch API and return up to ``limit`` candidates.

        Uses the MediaWiki opensearch endpoint — no API key required.
        Each result becomes a :class:`Candidate` with the Wikipedia article
        title as the locator (stable across fetches) and the article URL
        pre-computed for display.
        """
        if not query.strip():
            logger.info("wikipedia: empty query — returning no candidates")
            return []

        url = _OPENSEARCH_URL.format(query=quote(query), limit=limit)
        logger.info("wikipedia: searching %r (limit=%d)", query, limit)

        client = self._get_client()
        response = client.get(url)
        response.raise_for_status()

        # opensearch format: [query, [titles], [descriptions], [article_urls]]
        data: list[object] = response.json()
        titles: list[str] = cast(list[str], data[1]) if len(data) > 1 else []
        descriptions: list[str] = cast(list[str], data[2]) if len(data) > 2 else []
        article_urls: list[str] = cast(list[str], data[3]) if len(data) > 3 else []

        candidates: list[Candidate] = []
        for i, title in enumerate(titles):
            snippet = descriptions[i] if i < len(descriptions) else None
            locator = title  # stable Wikipedia title used in fetch()
            candidates.append(
                Candidate(
                    adapter=self.name,
                    locator=locator,
                    title=title,
                    snippet=snippet or None,
                    tier_hint=self.default_tier,
                )
            )
            logger.debug(
                "wikipedia: candidate %d — %r (%s)",
                i,
                title,
                article_urls[i] if i < len(article_urls) else "no url",
            )

        logger.info("wikipedia: found %d candidates for %r", len(candidates), query)
        return candidates

    def fetch(self, candidate: Candidate) -> FetchedArtifact:
        """Retrieve a Wikipedia article as plaintext via the extracts API.

        Writes the text to ``raw/documents/wikipedia_<slug>.md`` and a
        provenance sidecar alongside it. The source URL is set to the
        canonical ``https://en.wikipedia.org/wiki/<title>`` URL.

        Raises :class:`AdapterError` when:
        - ``candidate.adapter`` is not ``"wikipedia"``.
        - The MediaWiki API returns no pages.
        - The article extract is empty.
        """
        if candidate.adapter != self.name:
            raise AdapterError(
                f"wikipedia adapter cannot fetch candidate from adapter {candidate.adapter!r}"
            )

        title = candidate.locator
        api_url = _EXTRACTS_URL.format(title=quote(title))
        canonical_url = _WIKI_ARTICLE_URL.format(title=quote(title, safe=""))

        logger.info("wikipedia: fetching article %r", title)

        client = self._get_client()
        response = client.get(api_url)
        response.raise_for_status()

        data: dict[str, object] = response.json()
        query_block = data.get("query", {})
        pages: dict[str, object] = query_block.get("pages", {})  # type: ignore[union-attr]

        if not pages:
            raise AdapterError(f"wikipedia: no pages returned for title {title!r}")

        # MediaWiki uses a single page keyed by page ID (or "-1" for missing)
        page_id, page_data = next(iter(pages.items()))
        if page_id == "-1":
            raise AdapterError(f"wikipedia: article not found for title {title!r}")

        extract: str = page_data.get("extract", "")  # type: ignore[union-attr]
        resolved_title: str = page_data.get("title", title)  # type: ignore[union-attr]

        if not extract.strip():
            raise AdapterError(
                f"wikipedia: empty extract for article {resolved_title!r}"
            )

        slug = _title_to_slug(resolved_title)
        filename = f"wikipedia_{slug}.md"
        raw_dir = self.settings.RAW_DIR / "documents"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / filename

        content_bytes = extract.encode("utf-8")
        raw_path.write_bytes(content_bytes)
        logger.info(
            "wikipedia: wrote %d bytes to %s", len(content_bytes), raw_path
        )

        content_hash = sha256_bytes(content_bytes)
        source = Source(
            id=0,
            title=resolved_title,
            url=canonical_url,
            tier=candidate.tier_hint,
            adapter=self.name,
            checksum=content_hash,
            retrieved_at=datetime.now(),
            license=_LICENSE,
        )
        artifact = FetchedArtifact(
            raw_path=raw_path,
            source=source,
            content_hash=content_hash,
            license=_LICENSE,
        )
        write_sidecar(artifact)
        return artifact


def fetch_article_images(
    title: str,
    *,
    client: httpx.Client | None = None,
    limit: int = 5,
) -> list[dict[str, str]]:
    """Fetch image URLs and descriptions for a Wikipedia article.

    Returns a list of dicts with 'url', 'description', 'title' keys.
    Skips common non-content images (logos, icons, flags).
    """
    if client is None:
        from app.core.sources.fetcher import user_agent
        client = httpx.Client(
            timeout=30.0,
            headers={"User-Agent": user_agent()},
        )

    url = _IMAGES_URL.format(title=quote(title))
    response = client.get(url)
    response.raise_for_status()
    data = response.json()
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return []
    page = next(iter(pages.values()))
    image_titles = [
        img["title"]
        for img in page.get("images", [])
        if not any(img["title"].startswith(p) for p in _SKIP_IMAGE_PREFIXES)
    ]

    results: list[dict[str, str]] = []
    for file_title in image_titles[:limit]:
        try:
            info_url = _IMAGEINFO_URL.format(file_title=quote(file_title))
            info_resp = client.get(info_url)
            info_resp.raise_for_status()
            info_data = info_resp.json()
            info_pages = info_data.get("query", {}).get("pages", {})
            for info_page in info_pages.values():
                imageinfo = info_page.get("imageinfo", [])
                if not imageinfo:
                    continue
                ii = imageinfo[0]
                img_url = ii.get("url", "")
                if not img_url:
                    continue
                extmeta = ii.get("extmetadata", {})
                desc = extmeta.get("ImageDescription", {}).get("value", file_title)
                desc = re.sub(r"<[^>]+>", "", desc).strip()
                results.append(
                    {
                        "url": img_url,
                        "description": desc[:200],
                        "title": file_title,
                    }
                )
        except Exception as exc:
            logger.debug("wikipedia: failed to get info for %s: %s", file_title, exc)
            continue
    return results


__all__ = ["WikipediaAdapter", "fetch_article_images"]
