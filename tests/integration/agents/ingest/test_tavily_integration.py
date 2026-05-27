"""Integration test for TavilyAdapter — requires a live Tavily API key.

Marked ``@pytest.mark.network`` so it is excluded from standard CI runs.
Set ``KEBAB_TAVILY_API_KEY`` in the environment to enable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.config import Settings
from app.agents.ingest.adapters.tavily import TavilyAdapter


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    return Settings(
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="",
    )


def _skip_without_key(settings: Settings) -> None:
    if not settings.TAVILY_API_KEY:
        pytest.skip("KEBAB_TAVILY_API_KEY not set — skipping live Tavily test")


@pytest.mark.network
def test_tavily_live_search(settings: Settings) -> None:
    """Perform a real Tavily search and verify at least one candidate is returned."""
    _skip_without_key(settings)

    adapter = TavilyAdapter(settings=settings)
    candidates = adapter.discover("plate tectonics", limit=3)

    assert len(candidates) >= 1
    first = candidates[0]
    assert first.adapter == "tavily"
    assert first.locator.startswith("http")
    assert first.title
    assert first.tier_hint == 4


@pytest.mark.network
def test_tavily_include_domains_returns_results(settings: Settings) -> None:
    """Verify include_domains actually filters to the requested domains."""
    _skip_without_key(settings)

    adapter = TavilyAdapter(settings=settings)
    candidates = adapter.discover(
        "plate tectonics",
        limit=5,
        include_domains=["britannica.com", "nationalgeographic.com"],
    )

    # Should get at least one result from these well-known domains
    assert len(candidates) >= 1, (
        "include_domains returned 0 results for britannica.com + nationalgeographic.com — "
        "Tavily may not be filtering correctly"
    )
    for c in candidates:
        domain = c.locator.split("/")[2] if "/" in c.locator else c.locator
        assert any(
            auth in domain for auth in ("britannica.com", "nationalgeographic.com")
        ), f"Result {c.locator} is not from a requested domain"


@pytest.mark.network
def test_tavily_include_domains_narrow_returns_empty(settings: Settings) -> None:
    """Verify that a very narrow domain filter can return empty results.

    This confirms the fallback logic in searcher.py is actually needed.
    """
    _skip_without_key(settings)

    adapter = TavilyAdapter(settings=settings)
    candidates = adapter.discover(
        "aseismic creep transform fault mechanism",
        limit=3,
        include_domains=["deped.gov.ph"],
    )

    # deped.gov.ph is a Philippine education site — unlikely to have
    # deep geophysics content. This should return 0 or very few results.
    # We don't assert == 0 because search results are non-deterministic,
    # but this test documents the scenario that triggers the fallback.
    assert len(candidates) <= 1, (
        f"Expected 0-1 results from deped.gov.ph for a niche query, got {len(candidates)}"
    )


@pytest.mark.network
def test_tavily_include_domains_education_sources(settings: Settings) -> None:
    """Verify authoritative education sources return results for typical queries."""
    _skip_without_key(settings)

    adapter = TavilyAdapter(settings=settings)

    # Test each authoritative source individually to see which ones work
    sources = ["openstax.org", "khanacademy.org", "usgs.gov", "britannica.com"]
    results_by_source: dict[str, int] = {}

    for source in sources:
        candidates = adapter.discover(
            "types of plate boundaries",
            limit=3,
            include_domains=[source],
        )
        results_by_source[source] = len(candidates)

    # At least some of the authoritative sources should return results
    total = sum(results_by_source.values())
    assert total >= 1, f"No authoritative source returned results: {results_by_source}"

    # Log which sources work (useful for debugging)
    for source, count in results_by_source.items():
        print(f"  {source}: {count} result(s)")
