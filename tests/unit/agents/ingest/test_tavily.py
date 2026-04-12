"""Unit tests for the Tavily search adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config.config import Settings
from app.core.sources.adapter import AdapterError, Candidate, SourceAdapter
from app.agents.ingest.adapters.tavily import TavilyAdapter


def _settings(tmp_path: Path, *, tavily_api_key: str = "test-key") -> Settings:
    knowledge = tmp_path / "knowledge"
    return Settings(
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-google-key",
        TAVILY_API_KEY=tavily_api_key,
    )


def _fake_tavily_response(n: int = 3) -> dict[str, object]:
    """Build a fake Tavily search response with ``n`` results."""
    results = [
        {
            "url": f"https://example.com/article/{i}",
            "title": f"Article {i}",
            "content": f"Snippet for article {i}.",
        }
        for i in range(n)
    ]
    return {"results": results}


@pytest.mark.unit
class TestTavilyDiscover:
    def test_tavily_discover_returns_candidates(
        self, tmp_path: Path, mocker: pytest.FixtureRequest
    ) -> None:
        settings = _settings(tmp_path)
        adapter = TavilyAdapter(settings=settings)

        mock_client = MagicMock()
        mock_client.search.return_value = _fake_tavily_response(3)
        mocker.patch(
            "app.agents.ingest.adapters.tavily.TavilyClient",
            return_value=mock_client,
        )

        candidates = adapter.discover("plate tectonics")

        assert len(candidates) == 3
        first = candidates[0]
        assert first.adapter == "tavily"
        assert first.locator == "https://example.com/article/0"
        assert first.title == "Article 0"
        assert first.snippet == "Snippet for article 0."
        assert first.tier_hint == 4

    def test_tavily_discover_raises_without_api_key(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path, tavily_api_key="")
        adapter = TavilyAdapter(settings=settings)

        with pytest.raises(AdapterError, match="TAVILY_API_KEY"):
            adapter.discover("plate tectonics")

    def test_tavily_discover_respects_limit(
        self, tmp_path: Path, mocker: pytest.FixtureRequest
    ) -> None:
        settings = _settings(tmp_path)
        adapter = TavilyAdapter(settings=settings)

        mock_client = MagicMock()
        mock_client.search.return_value = _fake_tavily_response(5)
        mocker.patch(
            "app.agents.ingest.adapters.tavily.TavilyClient",
            return_value=mock_client,
        )

        adapter.discover("plate tectonics", limit=5)

        mock_client.search.assert_called_once_with("plate tectonics", max_results=5)

    def test_tavily_discover_skips_results_without_url(
        self, tmp_path: Path, mocker: pytest.FixtureRequest
    ) -> None:
        settings = _settings(tmp_path)
        adapter = TavilyAdapter(settings=settings)

        mock_client = MagicMock()
        mock_client.search.return_value = {
            "results": [
                {"url": "", "title": "No URL", "content": "skip me"},
                {"url": "https://example.com/good", "title": "Good", "content": "keep"},
            ]
        }
        mocker.patch(
            "app.agents.ingest.adapters.tavily.TavilyClient",
            return_value=mock_client,
        )

        candidates = adapter.discover("query")

        assert len(candidates) == 1
        assert candidates[0].locator == "https://example.com/good"


@pytest.mark.unit
class TestTavilyFetch:
    def _make_candidate(self, url: str = "https://example.com/page") -> Candidate:
        return Candidate(
            adapter="tavily",
            locator=url,
            title="Example Page",
            snippet="Some preview text.",
            tier_hint=4,
        )

    def test_tavily_fetch_writes_html_and_sidecar(
        self, tmp_path: Path, mocker: pytest.FixtureRequest
    ) -> None:
        settings = _settings(tmp_path)
        adapter = TavilyAdapter(settings=settings)

        html_content = b"<html><body>Hello</body></html>"
        mock_response = MagicMock()
        mock_response.content = html_content

        mock_fetcher = MagicMock()
        mock_fetcher.get.return_value = mock_response
        mock_fetcher.__enter__ = MagicMock(return_value=mock_fetcher)
        mock_fetcher.__exit__ = MagicMock(return_value=None)

        mocker.patch(
            "app.agents.ingest.adapters.tavily.SharedFetcher",
            return_value=mock_fetcher,
        )

        candidate = self._make_candidate("https://example.com/page")
        artifact = adapter.fetch(candidate)

        # Raw HTML file was written
        assert artifact.raw_path.exists()
        assert artifact.raw_path.read_bytes() == html_content
        assert artifact.raw_path.name.startswith("tavily_")
        assert artifact.raw_path.suffix == ".html"

        # Sidecar was written alongside the raw file
        sidecar = artifact.raw_path.parent / (artifact.raw_path.name + ".meta.json")
        assert sidecar.exists()

        # Artifact fields are populated
        assert artifact.source.adapter == "tavily"
        assert artifact.source.url == "https://example.com/page"
        assert artifact.source.tier == 4
        assert artifact.content_hash != ""

    def test_tavily_fetch_rejects_wrong_adapter(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        adapter = TavilyAdapter(settings=settings)

        wrong_candidate = Candidate(
            adapter="direct_url",
            locator="https://example.com/page",
            title="Some Page",
            snippet=None,
            tier_hint=4,
        )

        with pytest.raises(AdapterError, match="direct_url"):
            adapter.fetch(wrong_candidate)


@pytest.mark.unit
class TestTavilyProtocol:
    def test_tavily_protocol_conformance(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        adapter = TavilyAdapter(settings=settings)

        assert isinstance(adapter, SourceAdapter)

    def test_tavily_has_correct_name(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        adapter = TavilyAdapter(settings=settings)

        assert adapter.name == "tavily"

    def test_tavily_has_correct_default_tier(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        adapter = TavilyAdapter(settings=settings)

        assert adapter.default_tier == 4
