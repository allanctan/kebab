"""Integration test for TavilyAdapter — requires a live Tavily API key.

Marked ``@pytest.mark.network`` so it is excluded from standard CI runs.
Set ``KEBAB_TAVILY_API_KEY`` in the environment to enable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.config import Settings
from app.pipeline.ingest.adapters.tavily import TavilyAdapter


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


@pytest.mark.network
def test_tavily_live_search(settings: Settings) -> None:
    """Perform a real Tavily search and verify at least one candidate is returned.

    Skipped when ``KEBAB_TAVILY_API_KEY`` is not set.
    """
    if not settings.TAVILY_API_KEY:
        pytest.skip("KEBAB_TAVILY_API_KEY not set — skipping live Tavily search")

    adapter = TavilyAdapter(settings=settings)
    candidates = adapter.discover("plate tectonics", limit=3)

    assert len(candidates) >= 1
    first = candidates[0]
    assert first.adapter == "tavily"
    assert first.locator.startswith("http")
    assert first.title
    assert first.tier_hint == 4
