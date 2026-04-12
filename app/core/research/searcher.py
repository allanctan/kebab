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
from dataclasses import dataclass
from urllib.parse import quote

from app.agents.ingest.inbox import stage_to_inbox
from app.agents.ingest.registry import build_default_registry
from app.config.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceContent:
    """One fetched search result. Returned by :func:`search`."""

    title: str
    url: str
    content: str


def search(
    settings: Settings,
    adapter_name: str,
    query: str,
    *,
    limit: int = 2,
) -> list[SourceContent]:
    """Discover via the named adapter, fetch up to ``limit`` results, return content.

    Stages each fetched artifact in ``raw/inbox/`` for provenance.
    Unknown adapter names return ``[]`` with a warning. Per-candidate fetch
    failures are logged and skipped — never propagated.

    For Wikipedia candidates the locator is the article title; the canonical
    URL is constructed as ``https://en.wikipedia.org/wiki/<locator>``. For
    other adapters the locator is treated as a URL (with ``https://`` added
    if needed).
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

    candidates = adapter.discover(query, limit=max(limit + 1, 3))
    results: list[SourceContent] = []

    for candidate in candidates[:limit]:
        title = candidate.title
        locator = candidate.locator

        if adapter_name == "wikipedia":
            url = f"https://en.wikipedia.org/wiki/{quote(locator, safe='')}"
        else:
            url = locator if locator.startswith("http") else f"https://{locator}"

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


__all__ = ["SourceContent", "search"]
