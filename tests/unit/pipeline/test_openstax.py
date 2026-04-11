"""Unit tests for the OpenStax source adapter."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config.config import Settings
from app.core.sources.adapter import AdapterError, Candidate, SourceAdapter
from app.pipeline.ingest.adapters.openstax import OpenStaxAdapter


def _settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    return Settings(
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-google-key",
    )


def _search_response(items: list[dict[str, object]]) -> MagicMock:
    """Build a mock httpx.Response for the OpenStax CMS search endpoint."""
    mock = MagicMock()
    mock.json.return_value = {"items": items}
    return mock


def _book_items_fixture() -> list[dict[str, object]]:
    return [
        {
            "id": 185,
            "title": "Biology 2e",
            "meta": {"slug": "biology-2e", "type": "books.Book"},
            "description": "Biology 2e is designed to cover the scope...",
        }
    ]


def _book_details_response(
    slug: str,
    title: str,
    description: str,
    book_content: list[dict[str, object]],
) -> MagicMock:
    """Build a mock httpx.Response for the OpenStax CMS book details endpoint."""
    mock = MagicMock()
    mock.json.return_value = {
        "items": [
            {
                "id": 185,
                "title": title,
                "meta": {"slug": slug},
                "description": description,
                "book_content": book_content,
            }
        ]
    }
    return mock


_SAMPLE_BOOK_CONTENT: list[dict[str, object]] = [
    {"title": "Preface"},
    {
        "title": "Chapter 1: The Study of Life",
        "contents": [
            {"title": "1.1 The Science of Biology"},
            {"title": "1.2 Themes and Concepts of Biology"},
        ],
    },
]


@pytest.mark.unit
class TestOpenStaxDiscover:
    def test_openstax_discover_returns_candidates(
        self, tmp_path: Path, mocker: pytest.FixtureRequest
    ) -> None:
        settings = _settings(tmp_path)
        adapter = OpenStaxAdapter(settings=settings)

        mock_fetcher = MagicMock()
        mock_fetcher.get.return_value = _search_response(_book_items_fixture())
        mocker.patch(
            "app.pipeline.ingest.adapters.openstax.get_default_fetcher",
            return_value=mock_fetcher,
        )

        candidates = adapter.discover("biology")

        assert len(candidates) == 1
        first = candidates[0]
        assert first.adapter == "openstax"
        assert first.locator == "biology-2e"
        assert first.title == "Biology 2e"
        assert first.snippet == "Biology 2e is designed to cover the scope..."
        assert first.tier_hint == 2

    def test_openstax_discover_empty_results(
        self, tmp_path: Path, mocker: pytest.FixtureRequest
    ) -> None:
        settings = _settings(tmp_path)
        adapter = OpenStaxAdapter(settings=settings)

        mock_fetcher = MagicMock()
        mock_fetcher.get.return_value = _search_response([])
        mocker.patch(
            "app.pipeline.ingest.adapters.openstax.get_default_fetcher",
            return_value=mock_fetcher,
        )

        candidates = adapter.discover("xyzzy_no_books_expected")

        assert candidates == []

    def test_openstax_discover_empty_query_returns_no_candidates(
        self, tmp_path: Path, mocker: pytest.FixtureRequest
    ) -> None:
        settings = _settings(tmp_path)
        adapter = OpenStaxAdapter(settings=settings)

        mock_fetcher = MagicMock()
        mocker.patch(
            "app.pipeline.ingest.adapters.openstax.get_default_fetcher",
            return_value=mock_fetcher,
        )

        candidates = adapter.discover("   ")

        assert candidates == []
        mock_fetcher.get.assert_not_called()

    def test_openstax_discover_skips_items_without_slug(
        self, tmp_path: Path, mocker: pytest.FixtureRequest
    ) -> None:
        settings = _settings(tmp_path)
        adapter = OpenStaxAdapter(settings=settings)

        items: list[dict[str, object]] = [
            {"id": 1, "title": "No Slug Book", "meta": {}, "description": ""},
            {
                "id": 2,
                "title": "Has Slug Book",
                "meta": {"slug": "has-slug", "type": "books.Book"},
                "description": "Good book",
            },
        ]
        mock_fetcher = MagicMock()
        mock_fetcher.get.return_value = _search_response(items)
        mocker.patch(
            "app.pipeline.ingest.adapters.openstax.get_default_fetcher",
            return_value=mock_fetcher,
        )

        candidates = adapter.discover("test")

        assert len(candidates) == 1
        assert candidates[0].locator == "has-slug"


@pytest.mark.unit
class TestOpenStaxFetch:
    def _make_candidate(self, slug: str = "biology-2e") -> Candidate:
        return Candidate(
            adapter="openstax",
            locator=slug,
            title="Biology 2e",
            snippet="Biology 2e is designed to cover the scope...",
            tier_hint=2,
        )

    def test_openstax_fetch_writes_markdown_and_sidecar(
        self, tmp_path: Path, mocker: pytest.FixtureRequest
    ) -> None:
        settings = _settings(tmp_path)
        adapter = OpenStaxAdapter(settings=settings)

        mock_fetcher = MagicMock()
        mock_fetcher.get.return_value = _book_details_response(
            slug="biology-2e",
            title="Biology 2e",
            description="Biology 2e is designed to cover the scope and sequence requirements of a typical two-semester biology course.",
            book_content=_SAMPLE_BOOK_CONTENT,
        )
        mocker.patch(
            "app.pipeline.ingest.adapters.openstax.get_default_fetcher",
            return_value=mock_fetcher,
        )

        candidate = self._make_candidate("biology-2e")
        artifact = adapter.fetch(candidate)

        # Markdown file was written
        assert artifact.raw_path.exists()
        assert artifact.raw_path.suffix == ".md"
        assert artifact.raw_path.name.startswith("openstax_")

        content = artifact.raw_path.read_text(encoding="utf-8")
        assert "Biology 2e" in content
        assert "CC-BY-4.0" in content

        # Sidecar was written alongside the raw file
        sidecar_path = artifact.raw_path.parent / (artifact.raw_path.name + ".meta.json")
        assert sidecar_path.exists()

        sidecar_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert sidecar_data["license"] == "CC-BY-4.0"

        # License is CC-BY-4.0
        assert artifact.license == "CC-BY-4.0"
        assert artifact.source.license == "CC-BY-4.0"

        # Provenance fields are populated
        assert artifact.source.adapter == "openstax"
        assert artifact.source.tier == 2
        assert artifact.content_hash != ""

    def test_openstax_fetch_includes_table_of_contents(
        self, tmp_path: Path, mocker: pytest.FixtureRequest
    ) -> None:
        settings = _settings(tmp_path)
        adapter = OpenStaxAdapter(settings=settings)

        mock_fetcher = MagicMock()
        mock_fetcher.get.return_value = _book_details_response(
            slug="biology-2e",
            title="Biology 2e",
            description="A comprehensive biology textbook.",
            book_content=_SAMPLE_BOOK_CONTENT,
        )
        mocker.patch(
            "app.pipeline.ingest.adapters.openstax.get_default_fetcher",
            return_value=mock_fetcher,
        )

        artifact = adapter.fetch(self._make_candidate("biology-2e"))
        content = artifact.raw_path.read_text(encoding="utf-8")

        assert "Table of Contents" in content
        assert "Chapter 1: The Study of Life" in content
        assert "1.1 The Science of Biology" in content

    def test_openstax_fetch_rejects_wrong_adapter(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        adapter = OpenStaxAdapter(settings=settings)

        wrong_candidate = Candidate(
            adapter="wikipedia",
            locator="biology-2e",
            title="Biology 2e",
            snippet=None,
            tier_hint=2,
        )

        with pytest.raises(AdapterError, match="wikipedia"):
            adapter.fetch(wrong_candidate)

    def test_openstax_fetch_raises_when_no_items_returned(
        self, tmp_path: Path, mocker: pytest.FixtureRequest
    ) -> None:
        settings = _settings(tmp_path)
        adapter = OpenStaxAdapter(settings=settings)

        mock_fetcher = MagicMock()
        mock_fetcher.get.return_value = _search_response([])
        mocker.patch(
            "app.pipeline.ingest.adapters.openstax.get_default_fetcher",
            return_value=mock_fetcher,
        )

        candidate = self._make_candidate("no-such-book")
        with pytest.raises(AdapterError, match="no book found"):
            adapter.fetch(candidate)

    def test_openstax_fetch_raises_when_content_is_empty(
        self, tmp_path: Path, mocker: pytest.FixtureRequest
    ) -> None:
        settings = _settings(tmp_path)
        adapter = OpenStaxAdapter(settings=settings)

        mock_fetcher = MagicMock()
        mock_fetcher.get.return_value = _book_details_response(
            slug="empty-book",
            title="Empty Book",
            description="",
            book_content=[],
        )
        mocker.patch(
            "app.pipeline.ingest.adapters.openstax.get_default_fetcher",
            return_value=mock_fetcher,
        )

        candidate = Candidate(
            adapter="openstax",
            locator="empty-book",
            title="Empty Book",
            snippet=None,
            tier_hint=2,
        )
        with pytest.raises(AdapterError, match="no usable content"):
            adapter.fetch(candidate)

    def test_openstax_fetch_sets_canonical_url(
        self, tmp_path: Path, mocker: pytest.FixtureRequest
    ) -> None:
        settings = _settings(tmp_path)
        adapter = OpenStaxAdapter(settings=settings)

        mock_fetcher = MagicMock()
        mock_fetcher.get.return_value = _book_details_response(
            slug="biology-2e",
            title="Biology 2e",
            description="Biology content here.",
            book_content=[],
        )
        mocker.patch(
            "app.pipeline.ingest.adapters.openstax.get_default_fetcher",
            return_value=mock_fetcher,
        )

        artifact = adapter.fetch(self._make_candidate("biology-2e"))

        assert artifact.source.url == "https://openstax.org/details/books/biology-2e"


@pytest.mark.unit
class TestOpenStaxProtocol:
    def test_openstax_protocol_conformance(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        adapter = OpenStaxAdapter(settings=settings)

        assert isinstance(adapter, SourceAdapter)

    def test_openstax_tier_is_2(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        adapter = OpenStaxAdapter(settings=settings)

        assert adapter.default_tier == 2

    def test_openstax_name_is_openstax(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        adapter = OpenStaxAdapter(settings=settings)

        assert adapter.name == "openstax"
