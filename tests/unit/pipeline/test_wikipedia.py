"""Unit tests for the Wikipedia source adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config.config import Settings
from app.core.sources.adapter import AdapterError, Candidate, SourceAdapter
from app.pipeline.ingest.adapters.wikipedia import WikipediaAdapter


def _settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    return Settings(
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-google-key",
    )


def _opensearch_response(
    query: str,
    titles: list[str],
    descriptions: list[str],
    urls: list[str],
) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = [query, titles, descriptions, urls]
    mock.raise_for_status = MagicMock()
    return mock


def _extracts_response(page_id: str, title: str, extract: str) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = {
        "query": {
            "pages": {
                page_id: {
                    "pageid": int(page_id) if page_id != "-1" else -1,
                    "title": title,
                    "extract": extract,
                }
            }
        }
    }
    mock.raise_for_status = MagicMock()
    return mock


def _mock_client() -> MagicMock:
    return MagicMock()


@pytest.mark.unit
class TestWikipediaDiscover:
    def test_wikipedia_discover_returns_candidates(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        client = _mock_client()
        client.get.return_value = _opensearch_response(
            query="photosynthesis",
            titles=["Photosynthesis", "Photosynthesis (disambiguation)"],
            descriptions=["The process by which plants...", "Disambiguation page"],
            urls=[
                "https://en.wikipedia.org/wiki/Photosynthesis",
                "https://en.wikipedia.org/wiki/Photosynthesis_(disambiguation)",
            ],
        )
        adapter = WikipediaAdapter(settings=settings, _client=client)

        candidates = adapter.discover("photosynthesis")

        assert len(candidates) == 2
        first = candidates[0]
        assert first.adapter == "wikipedia"
        assert first.locator == "Photosynthesis"
        assert first.title == "Photosynthesis"
        assert first.snippet == "The process by which plants..."
        assert first.tier_hint == 4

    def test_wikipedia_discover_empty_query(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        client = _mock_client()
        adapter = WikipediaAdapter(settings=settings, _client=client)

        candidates = adapter.discover("   ")

        assert candidates == []
        client.get.assert_not_called()

    def test_wikipedia_discover_respects_limit(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        client = _mock_client()
        client.get.return_value = _opensearch_response(
            query="cell biology",
            titles=["Cell biology"],
            descriptions=["The study of cells"],
            urls=["https://en.wikipedia.org/wiki/Cell_biology"],
        )
        adapter = WikipediaAdapter(settings=settings, _client=client)

        adapter.discover("cell biology", limit=5)

        called_url: str = client.get.call_args[0][0]
        assert "limit=5" in called_url

    def test_wikipedia_discover_handles_empty_results(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        client = _mock_client()
        client.get.return_value = _opensearch_response(
            query="xyzzy_no_results_expected",
            titles=[],
            descriptions=[],
            urls=[],
        )
        adapter = WikipediaAdapter(settings=settings, _client=client)

        candidates = adapter.discover("xyzzy_no_results_expected")

        assert candidates == []


@pytest.mark.unit
class TestWikipediaFetch:
    def _make_candidate(self, title: str = "Photosynthesis") -> Candidate:
        return Candidate(
            adapter="wikipedia",
            locator=title,
            title=title,
            snippet="The process by which plants make food.",
            tier_hint=4,
        )

    def test_wikipedia_fetch_writes_markdown_and_sidecar(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        extract_text = (
            "Photosynthesis is a process used by plants and other organisms "
            "to convert light energy into chemical energy."
        )
        client = _mock_client()
        client.get.return_value = _extracts_response(
            page_id="45833", title="Photosynthesis", extract=extract_text,
        )
        adapter = WikipediaAdapter(settings=settings, _client=client)

        artifact = adapter.fetch(self._make_candidate("Photosynthesis"))

        assert artifact.raw_path.exists()
        assert artifact.raw_path.suffix == ".md"
        assert artifact.raw_path.name.startswith("wikipedia_")
        assert artifact.raw_path.read_text(encoding="utf-8") == extract_text
        sidecar_path = artifact.raw_path.parent / (artifact.raw_path.name + ".meta.json")
        assert sidecar_path.exists()
        assert artifact.license == "CC-BY-SA-3.0"
        assert artifact.source.license == "CC-BY-SA-3.0"
        assert artifact.source.adapter == "wikipedia"
        assert artifact.source.tier == 4

    def test_wikipedia_fetch_rejects_wrong_adapter(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        adapter = WikipediaAdapter(settings=settings)
        wrong_candidate = Candidate(
            adapter="tavily", locator="Photosynthesis", title="Photosynthesis",
            snippet=None, tier_hint=4,
        )
        with pytest.raises(AdapterError, match="tavily"):
            adapter.fetch(wrong_candidate)

    def test_wikipedia_fetch_raises_on_missing_article(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "query": {"pages": {"-1": {"title": "Nonexistent", "missing": ""}}}
        }
        mock_response.raise_for_status = MagicMock()
        client = _mock_client()
        client.get.return_value = mock_response
        adapter = WikipediaAdapter(settings=settings, _client=client)

        candidate = Candidate(
            adapter="wikipedia", locator="Nonexistent", title="Nonexistent",
            snippet=None, tier_hint=4,
        )
        with pytest.raises(AdapterError, match="not found"):
            adapter.fetch(candidate)

    def test_wikipedia_fetch_raises_on_empty_extract(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        client = _mock_client()
        client.get.return_value = _extracts_response(
            page_id="99999", title="Empty Article", extract="   ",
        )
        adapter = WikipediaAdapter(settings=settings, _client=client)

        candidate = Candidate(
            adapter="wikipedia", locator="Empty Article", title="Empty Article",
            snippet=None, tier_hint=4,
        )
        with pytest.raises(AdapterError, match="empty extract"):
            adapter.fetch(candidate)

    def test_wikipedia_fetch_follows_redirects(self, tmp_path: Path) -> None:
        """Redirect pages should return the target article's content."""
        settings = _settings(tmp_path)
        client = _mock_client()
        # Simulate a redirect response: API returns the resolved title
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "query": {
                "redirects": [{"from": "Divergent plate boundary", "to": "Divergent boundary"}],
                "pages": {
                    "12345": {
                        "pageid": 12345,
                        "title": "Divergent boundary",
                        "extract": "A divergent boundary is a linear feature...",
                    }
                },
            }
        }
        mock_response.raise_for_status = MagicMock()
        client.get.return_value = mock_response
        adapter = WikipediaAdapter(settings=settings, _client=client)

        candidate = self._make_candidate("Divergent plate boundary")
        artifact = adapter.fetch(candidate)

        assert artifact.raw_path.exists()
        assert "divergent boundary" in artifact.raw_path.read_text(encoding="utf-8").lower()
        assert artifact.source.title == "Divergent boundary"

    def test_wikipedia_fetch_sets_correct_url(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        client = _mock_client()
        client.get.return_value = _extracts_response(
            page_id="45833", title="Photosynthesis", extract="Photosynthesis is...",
        )
        adapter = WikipediaAdapter(settings=settings, _client=client)

        artifact = adapter.fetch(self._make_candidate("Photosynthesis"))

        assert artifact.source.url is not None
        assert artifact.source.url.startswith("https://en.wikipedia.org/wiki/")
        assert "Photosynthesis" in artifact.source.url


@pytest.mark.unit
class TestWikipediaProtocol:
    def test_wikipedia_protocol_conformance(self, tmp_path: Path) -> None:
        adapter = WikipediaAdapter(settings=_settings(tmp_path))
        assert isinstance(adapter, SourceAdapter)

    def test_wikipedia_has_correct_name(self, tmp_path: Path) -> None:
        adapter = WikipediaAdapter(settings=_settings(tmp_path))
        assert adapter.name == "wikipedia"

    def test_wikipedia_has_correct_default_tier(self, tmp_path: Path) -> None:
        adapter = WikipediaAdapter(settings=_settings(tmp_path))
        assert adapter.default_tier == 4
