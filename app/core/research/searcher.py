"""Shared search plumbing for the research-* agents.

Pure adapter dispatch with no LLM calls. Discovers candidates via the
named adapter, fetches up to ``limit`` of them, stages each fetched
artifact in ``raw/inbox/`` for provenance, and returns the decoded text
plus title and canonical URL.

Lifted from ``app/agents/research/agent.py::_default_searcher`` as part
of the research restructure (2026-04-12 spec). The only API change is the
return type: ``list[SourceContent]`` instead of ``list[tuple[str, str, str]]``.

TODO: ``app/agents/ingest/registry.py`` is the only ``agents/`` module
that ``core/`` imports from. The registry is a lookup table, not an
orchestrator, and is "core-like code that lives in agents/ by historical
accident." Promoting it to ``core/ingest/registry.py`` is tracked as a
follow-up; until then, this import is the sole tolerated exception to the
strictly-downward layering rule documented in
``docs/superpowers/specs/2026-04-12-research-restructure-design.md``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

from app.agents.ingest.inbox import inbox_path, stage_to_inbox
from app.agents.ingest.registry import build_default_registry
from app.config.config import Settings
from app.core.sources.adapter import SourceAdapter

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _inbox_filename(adapter_name: str, locator: str) -> str:
    """Derive the expected inbox filename for a candidate.

    Mirrors the naming convention used by the adapters:
    - tavily: ``research_tavily_{url_slug}.html``
    - wikipedia: ``research_wikipedia_{title_slug}.md``
    """
    if adapter_name == "wikipedia":
        slug = _SLUG_RE.sub("-", locator.lower()).strip("-")[:60]
        return f"research_wikipedia_{slug}.md"
    parsed = urlparse(locator)
    raw = (parsed.netloc + parsed.path).lower()
    slug = _SLUG_RE.sub("-", raw).strip("-")[:60]
    return f"research_tavily_{slug}.html"


def _read_from_cache(
    knowledge_dir: Path, adapter_name: str, locator: str
) -> str | None:
    """Return cached content from inbox if available, else None."""
    filename = _inbox_filename(adapter_name, locator)
    cached = inbox_path(knowledge_dir) / filename
    if cached.exists():
        logger.debug("searcher: cache hit for %s", filename)
        return cached.read_bytes().decode("utf-8", errors="replace")
    return None


@dataclass(frozen=True)
class SourceContent:
    """One fetched search result. Returned by :func:`search`."""

    title: str
    url: str
    content: str


def _fetch_results(
    adapter: SourceAdapter,
    query: str,
    settings: Settings,
    limit: int,
    *,
    include_domains: list[str] | None = None,
) -> list[SourceContent]:
    """Discover via ``adapter``, fetch up to ``limit`` results, stage for provenance.

    Internal helper used by :func:`search` to avoid repeating the discover→fetch
    loop across the authoritative / wikipedia / general fallback chain.

    ``include_domains`` is only forwarded when ``adapter.name`` is ``"tavily"``
    and the list is non-empty — the Tavily adapter is the only one that supports
    this parameter.
    """
    adapter_name = adapter.name
    try:
        if include_domains and adapter_name == "tavily":
            candidates = adapter.discover(
                query,
                limit=max(limit + 1, 3),
                include_domains=include_domains,  # type: ignore[call-arg] — TavilyAdapter accepts include_domains, not part of SourceAdapter Protocol
            )
        else:
            candidates = adapter.discover(query, limit=max(limit + 1, 3))
    except Exception as exc:  # noqa: BLE001 — keep research run alive on transient adapter errors
        # Adapter-level discovery failures (rate limits, 5xx, network) must not
        # kill the entire research run. Log and return no candidates; the next
        # query (or article) gets a fresh attempt.
        logger.warning(
            "searcher: discover failed via %s for %r — %s",
            adapter_name,
            query,
            exc,
        )
        return []

    results: list[SourceContent] = []

    for candidate in candidates[:limit]:
        title = candidate.title
        locator = candidate.locator

        if adapter_name == "wikipedia":
            url = f"https://en.wikipedia.org/wiki/{quote(locator, safe='')}"
        else:
            url = locator if locator.startswith("http") else f"https://{locator}"

        # Check inbox cache before fetching from the network.
        cached = _read_from_cache(settings.KNOWLEDGE_DIR, adapter_name, locator)
        if cached is not None:
            content = cached
        else:
            try:
                artifact = adapter.fetch(candidate)
                content_bytes = artifact.raw_path.read_bytes()
                content = content_bytes.decode("utf-8", errors="replace")
                filename = f"research_{artifact.raw_path.name}"
                stage_to_inbox(settings.KNOWLEDGE_DIR, filename, content_bytes)
            except Exception as exc:
                logger.warning(
                    "searcher: fetch failed for %r (%s) — %s", title, url, exc
                )
                continue

        results.append(SourceContent(title=title, url=url, content=content))

    return results


def search(
    settings: Settings,
    adapter_name: str,
    query: str,
    *,
    limit: int = 2,
    authoritative_sources: list[str] | None = None,
) -> list[SourceContent]:
    """Discover via the named adapter, fetch up to ``limit`` results, return content.

    Stages each fetched artifact in ``raw/inbox/`` for provenance.
    Unknown adapter names return ``[]`` with a warning. Per-candidate fetch
    failures are logged and skipped — never propagated.

    For Wikipedia candidates the locator is the article title; the canonical
    URL is constructed as ``https://en.wikipedia.org/wiki/<locator>``. For
    other adapters the locator is treated as a URL (with ``https://`` added
    if needed).

    When ``authoritative_sources`` is non-empty and ``adapter_name`` is
    ``"tavily"``, the search follows a strict two-step chain:

    1. Tavily restricted to ``authoritative_sources`` (``include_domains``).
    2. Wikipedia (if step 1 returns nothing).

    If both steps return nothing, the function returns ``[]`` — it does
    NOT fall back to general Tavily. This keeps low-quality sources
    (Reddit, Quora, homework sites) out of gap answers and claim
    verifications.

    For all other adapters or when ``authoritative_sources`` is ``None`` /
    empty, the function behaves exactly as before (single adapter, no fallback).
    """
    registry = build_default_registry(settings)

    try:
        adapter = registry.get(adapter_name)
    except Exception:
        logger.warning(
            "searcher: unknown adapter %r — skipping query %r",
            adapter_name,
            query,
        )
        return []

    # Authoritative-source priority chain (tavily only).
    if authoritative_sources and adapter_name == "tavily":
        # Step 1: Tavily restricted to authoritative domains.
        results = _fetch_results(
            adapter,
            query,
            settings,
            limit,
            include_domains=authoritative_sources,
        )
        if results:
            return results

        # Step 2: Wikipedia fallback.
        try:
            wiki_adapter = registry.get("wikipedia")
        except Exception:
            wiki_adapter = None

        if wiki_adapter is not None:
            results = _fetch_results(wiki_adapter, query, settings, limit)
            if results:
                return results

        # No general-Tavily fallback — gap stays unanswered rather than
        # accepting low-quality sources.
        logger.info(
            "searcher: no authoritative or Wikipedia results for %r — returning empty",
            query,
        )
        return []

    # Default path: single adapter, no fallback.
    return _fetch_results(adapter, query, settings, limit)


__all__ = ["SourceContent", "search"]
