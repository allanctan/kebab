"""Web scraper test using a mocked httpx client."""

from __future__ import annotations

import httpx
import pytest

from app.core.errors import IngestError
from app.utils import web_scraper


def test_fetch_returns_html_and_text(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<html><body><h1>Hi</h1><p>there</p></body></html>"

    def fake_get(self: httpx.Client, url: str) -> httpx.Response:
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    raw, text = web_scraper.fetch("https://example.test")
    assert raw == html
    assert "Hi" in text
    assert "there" in text


def test_fetch_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(self: httpx.Client, url: str) -> httpx.Response:
        return httpx.Response(500, text="boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    with pytest.raises(IngestError):
        web_scraper.fetch("https://example.test")
