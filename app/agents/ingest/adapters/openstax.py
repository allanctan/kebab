"""OpenStax adapter — uses the OpenStax CMS API for discovery and fetch.

``discover(query)`` queries the OpenStax CMS API for books matching the
search term and returns a ranked list of :class:`Candidate` references.
``fetch(candidate)`` retrieves the book's overview details via the same
API and writes a markdown summary to ``raw/documents/openstax_<slug>.md``.

No API key required — OpenStax is freely accessible.

Why OpenStax at tier 2? OpenStax books are peer-reviewed, authored by
subject-matter experts, and published under CC-BY-4.0. They are among
the highest-quality openly licensed educational resources available.

License is always ``CC-BY-4.0``.

Pattern adapted from better-ed-ai/app/agents/assignment/assignment_checker.py
and the KEBAB wikipedia adapter.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

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

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_SEARCH_URL = (
    "https://openstax.org/apps/cms/api/v2/pages/"
    "?type=books.Book&fields=title,description&search={query}&limit={limit}"
)
_BOOK_DETAILS_URL = (
    "https://openstax.org/apps/cms/api/v2/pages/"
    "?type=books.Book&fields=title,description,book_content&slug={slug}"
)
_BOOK_WEB_URL = "https://openstax.org/details/books/{slug}"
_LICENSE = "CC-BY-4.0"


def _slug_to_filename_slug(slug: str, *, max_len: int = 60) -> str:
    """Derive a safe filesystem slug from an OpenStax book slug.

    Lowercases, replaces runs of non-alphanumeric characters with hyphens,
    and truncates to ``max_len`` characters.
    """
    clean = _SLUG_NON_ALNUM.sub("-", slug.lower()).strip("-")
    return clean[:max_len]


def _build_markdown(title: str, slug: str, description: str, book_content: list[dict[str, object]]) -> str:
    """Render a book overview as markdown from the API response data."""
    lines: list[str] = [
        f"# {title}",
        "",
        f"**Source:** {_BOOK_WEB_URL.format(slug=slug)}",
        f"**License:** {_LICENSE}",
        "",
        "## Description",
        "",
        description.strip() if description.strip() else "_No description available._",
        "",
    ]

    if book_content:
        lines += ["## Table of Contents", ""]
        for chapter in book_content:
            chapter_title = str(chapter.get("title", ""))
            if chapter_title:
                lines.append(f"- {chapter_title}")
            sub_contents: list[dict[str, object]] = chapter.get("contents", [])  # type: ignore[assignment]
            for sub in sub_contents or []:
                sub_title = str(sub.get("title", ""))
                if sub_title:
                    lines.append(f"  - {sub_title}")
        lines.append("")

    return "\n".join(lines)


@dataclass
class OpenStaxAdapter:
    """OpenStax source adapter. Default tier 2 (peer-reviewed)."""

    settings: Settings
    name: ClassVar[str] = "openstax"
    default_tier: SourceTier = field(default=2)
    _fetcher: SharedFetcher | None = field(default=None, repr=False)

    def _get_fetcher(self) -> SharedFetcher:
        if self._fetcher is None:
            self._fetcher = get_default_fetcher(self.settings)
        return self._fetcher

    def discover(self, query: str, *, limit: int = 10) -> list[Candidate]:
        """Search the OpenStax CMS API and return up to ``limit`` candidates.

        Uses the OpenStax CMS pages API — no API key required. Each book
        result becomes a :class:`Candidate` with the book slug as the
        locator (stable, used by ``fetch()``).
        """
        if not query.strip():
            logger.info("openstax: empty query — returning no candidates")
            return []

        url = _SEARCH_URL.format(query=query, limit=limit)
        logger.info("openstax: searching %r (limit=%d)", query, limit)

        fetcher = self._get_fetcher()
        response = fetcher.get(url)

        data: dict[str, object] = response.json()
        items: list[dict[str, object]] = data.get("items", [])  # type: ignore[assignment]

        candidates: list[Candidate] = []
        for item in items:
            meta: dict[str, object] = item.get("meta", {})  # type: ignore[assignment]
            slug = str(meta.get("slug", ""))
            title = str(item.get("title", slug))
            description = str(item.get("description", "")) or None
            if not slug:
                logger.debug("openstax: skipping item with no slug: %r", item)
                continue
            candidates.append(
                Candidate(
                    adapter=self.name,
                    locator=slug,
                    title=title,
                    snippet=description,
                    tier_hint=self.default_tier,
                )
            )
            logger.debug("openstax: candidate — slug=%r title=%r", slug, title)

        logger.info("openstax: found %d candidates for %r", len(candidates), query)
        return candidates

    def fetch(self, candidate: Candidate) -> FetchedArtifact:
        """Retrieve an OpenStax book overview and write it as markdown.

        Fetches the book's CMS details (title, description, table of
        contents) and writes them to ``raw/documents/openstax_<slug>.md``
        plus a provenance sidecar alongside it.

        Raises :class:`AdapterError` when:
        - ``candidate.adapter`` is not ``"openstax"``.
        - The CMS API returns no items for the given slug.
        - The book data contains no usable content.
        """
        if candidate.adapter != self.name:
            raise AdapterError(
                f"openstax adapter cannot fetch candidate from adapter {candidate.adapter!r}"
            )

        slug = candidate.locator
        api_url = _BOOK_DETAILS_URL.format(slug=slug)
        canonical_url = _BOOK_WEB_URL.format(slug=slug)

        logger.info("openstax: fetching book %r", slug)

        fetcher = self._get_fetcher()
        response = fetcher.get(api_url)

        data: dict[str, object] = response.json()
        items: list[dict[str, object]] = data.get("items", [])  # type: ignore[assignment]

        if not items:
            raise AdapterError(f"openstax: no book found for slug {slug!r}")

        book = items[0]
        title = str(book.get("title", slug))
        description = str(book.get("description", ""))
        book_content: list[dict[str, object]] = book.get("book_content", []) or []  # type: ignore[assignment]

        if not description.strip() and not book_content:
            raise AdapterError(
                f"openstax: no usable content for book {slug!r} (title={title!r})"
            )

        markdown = _build_markdown(title, slug, description, book_content)

        filename_slug = _slug_to_filename_slug(slug)
        filename = f"openstax_{filename_slug}.md"
        raw_dir = self.settings.RAW_DIR / "documents"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / filename

        content_bytes = markdown.encode("utf-8")
        raw_path.write_bytes(content_bytes)
        logger.info("openstax: wrote %d bytes to %s", len(content_bytes), raw_path)

        content_hash = sha256_bytes(content_bytes)
        source = Source(
            id=0,
            title=title,
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


__all__ = ["OpenStaxAdapter"]
