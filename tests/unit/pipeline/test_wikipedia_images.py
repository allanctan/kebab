"""Tests for Wikipedia image fetching."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.pipeline.ingest.adapters.wikipedia import fetch_article_images


def _mock_client() -> MagicMock:
    return MagicMock()


class TestFetchArticleImages:
    def test_returns_image_info(self) -> None:
        client = _mock_client()
        images_response = MagicMock()
        images_response.json.return_value = {
            "query": {"pages": {"12345": {"images": [
                {"title": "File:Plate boundaries.svg"},
                {"title": "File:Wiki-logo.png"},  # should be skipped
            ]}}}
        }
        images_response.raise_for_status = MagicMock()

        info_response = MagicMock()
        info_response.json.return_value = {
            "query": {"pages": {"-1": {
                "title": "File:Plate boundaries.svg",
                "imageinfo": [{"url": "https://upload.wikimedia.org/Plate_boundaries.svg",
                               "extmetadata": {"ImageDescription": {"value": "Map of plate boundaries"}}}],
            }}}
        }
        info_response.raise_for_status = MagicMock()
        client.get.side_effect = [images_response, info_response]

        images = fetch_article_images("Plate tectonics", client=client, limit=5)
        assert len(images) == 1
        assert images[0]["url"].startswith("https://")
        assert "plate boundaries" in images[0]["description"].lower()

    def test_empty_when_no_images(self) -> None:
        client = _mock_client()
        response = MagicMock()
        response.json.return_value = {"query": {"pages": {"123": {"images": []}}}}
        response.raise_for_status = MagicMock()
        client.get.return_value = response
        assert fetch_article_images("Empty", client=client) == []

    def test_skips_wiki_logos(self) -> None:
        client = _mock_client()
        response = MagicMock()
        response.json.return_value = {
            "query": {"pages": {"123": {"images": [
                {"title": "File:Wiki-logo.png"},
                {"title": "File:Commons-logo.svg"},
                {"title": "File:Symbol support vote.svg"},
            ]}}}
        }
        response.raise_for_status = MagicMock()
        client.get.return_value = response
        assert fetch_article_images("Test", client=client) == []

    def test_strips_html_from_description(self) -> None:
        client = _mock_client()
        images_response = MagicMock()
        images_response.json.return_value = {
            "query": {"pages": {"1": {"images": [{"title": "File:Test.png"}]}}}
        }
        images_response.raise_for_status = MagicMock()

        info_response = MagicMock()
        info_response.json.return_value = {
            "query": {"pages": {"-1": {
                "title": "File:Test.png",
                "imageinfo": [{"url": "https://example.com/test.png",
                               "extmetadata": {"ImageDescription": {"value": "<b>Bold</b> description"}}}],
            }}}
        }
        info_response.raise_for_status = MagicMock()
        client.get.side_effect = [images_response, info_response]

        images = fetch_article_images("Test", client=client)
        assert images[0]["description"] == "Bold description"
