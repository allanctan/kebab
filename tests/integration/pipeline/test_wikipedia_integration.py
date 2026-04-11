"""Integration test for WikipediaAdapter — requires outbound network access.

Marked ``@pytest.mark.network`` so it is excluded from standard CI runs.
No API key is required; Wikipedia is freely accessible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.config import Settings
from app.pipeline.ingest.adapters.wikipedia import WikipediaAdapter


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
def test_wikipedia_live_search(settings: Settings) -> None:
    """Perform a real Wikipedia opensearch and verify at least one candidate is returned."""
    try:
        adapter = WikipediaAdapter(settings=settings)
        candidates = adapter.discover("photosynthesis", limit=5)
    except Exception as exc:
        pytest.skip(f"Wikipedia API not available: {exc}")

    assert len(candidates) >= 1
    first = candidates[0]
    assert first.adapter == "wikipedia"
    assert first.title
    assert first.tier_hint == 4
