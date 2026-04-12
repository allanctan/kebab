"""Tavily search adapter — wraps the ``tavily-python`` SDK for web discovery.

``discover(query)`` calls the Tavily search API and returns a ranked list of
:class:`Candidate` references. ``fetch(candidate)`` downloads the candidate's
URL through :class:`SharedFetcher` (robots.txt + rate-limiting) and writes the
raw HTML under ``raw/documents/tavily_<slug>.html``.

Why Tavily at tier 4?  Tavily returns links from reputable platforms (news,
wikis, academic sites) but the caller does not know the upstream publisher
authority — that's determined later during curate. We start at tier 4
("reputable platform") so reviewers see it conservatively and can upgrade
after inspection.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar
from urllib.parse import urlparse

from tavily import TavilyClient  # type: ignore[import-untyped]

from app.config.config import Settings
from app.core.sources.fetcher import SharedFetcher, get_default_fetcher
from app.core.sources.provenance import sha256_bytes, write_sidecar
from app.core.sources.adapter import (
    AdapterError,
    Candidate,
    FetchedArtifact,
    SourceTier,
)
from app.models.source import Source

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _url_to_slug(url: str, *, max_len: int = 60) -> str:
    """Derive a safe filesystem slug from a URL.

    Strips scheme/query/fragment, lowercases, replaces non-alphanumeric
    runs with hyphens, and truncates to ``max_len`` characters.
    """
    parsed = urlparse(url)
    raw = (parsed.netloc + parsed.path).lower()
    slug = _SLUG_RE.sub("-", raw).strip("-")
    return slug[:max_len]


@dataclass
class TavilyAdapter:
    """Tavily web-search adapter. Default tier 4 (reputable platform)."""

    settings: Settings
    name: ClassVar[str] = "tavily"
    default_tier: SourceTier = field(default=4)
    _fetcher: SharedFetcher | None = field(default=None, repr=False)

    def _get_fetcher(self) -> SharedFetcher:
        if self._fetcher is None:
            self._fetcher = get_default_fetcher(self.settings)
        return self._fetcher

    def discover(self, query: str, *, limit: int = 10) -> list[Candidate]:
        """Search Tavily for ``query`` and return up to ``limit`` candidates.

        Raises :class:`AdapterError` when ``TAVILY_API_KEY`` is not configured.
        Each result becomes a :class:`Candidate` with the result URL as locator
        and the Tavily-provided title/snippet populated.
        """
        if not self.settings.TAVILY_API_KEY:
            raise AdapterError(
                "TAVILY_API_KEY is not configured — set KEBAB_TAVILY_API_KEY in the environment."
            )

        client = TavilyClient(api_key=self.settings.TAVILY_API_KEY)
        logger.info("tavily: searching %r (limit=%d)", query, limit)

        response = client.search(query, max_results=limit)
        results: list[dict[str, object]] = response.get("results", [])

        candidates: list[Candidate] = []
        for result in results:
            url = str(result.get("url", ""))
            title = str(result.get("title", url))
            snippet = str(result.get("content", "")) or None
            if not url:
                continue
            candidates.append(
                Candidate(
                    adapter=self.name,
                    locator=url,
                    title=title,
                    snippet=snippet,
                    tier_hint=self.default_tier,
                )
            )

        logger.info("tavily: found %d candidates for %r", len(candidates), query)
        return candidates

    def fetch(self, candidate: Candidate) -> FetchedArtifact:
        """Download ``candidate.locator`` via :class:`SharedFetcher`.

        Writes the raw HTML to ``raw/documents/tavily_<slug>.html`` and a
        provenance sidecar alongside it. Returns the populated
        :class:`FetchedArtifact`.

        Raises :class:`AdapterError` when ``candidate.adapter`` is not
        ``"tavily"``.
        """
        if candidate.adapter != self.name:
            raise AdapterError(
                f"tavily adapter cannot fetch candidate from adapter {candidate.adapter!r}"
            )

        url = candidate.locator
        slug = _url_to_slug(url)
        filename = f"tavily_{slug}.html"
        raw_dir = self.settings.RAW_DIR / "documents"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / filename

        fetcher = self._get_fetcher()
        response = fetcher.get(url)

        html_bytes = response.content
        raw_path.write_bytes(html_bytes)
        logger.info("tavily: wrote %d bytes to %s", len(html_bytes), raw_path)

        content_hash = sha256_bytes(html_bytes)
        source = Source(
            id=0,
            title=candidate.title,
            url=url,
            tier=candidate.tier_hint,
            adapter=self.name,
            checksum=content_hash,
            retrieved_at=datetime.now(),
        )
        artifact = FetchedArtifact(
            raw_path=raw_path,
            source=source,
            content_hash=content_hash,
        )
        write_sidecar(artifact)
        return artifact


__all__ = ["TavilyAdapter"]
