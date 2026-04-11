"""Shared HTTP fetcher — allowlist, robots, rate limiting, backoff."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.config.config import Settings
from app.core.sources.fetcher import (
    FetchBlockedError,
    FetchError,
    FetchTransientError,
    SharedFetcher,
)


def _settings(
    tmp_path: Path, *, allowed: list[str] | None = None
) -> Settings:
    return Settings(
        KNOWLEDGE_DIR=tmp_path,
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
        ALLOWED_SOURCE_DOMAINS=allowed or [],
    )


def _fetcher_with_transport(
    settings: Settings, transport: httpx.MockTransport, *, rate_per_sec: float = 1000.0
) -> SharedFetcher:
    """Build a fetcher, then replace its client with one using the mock transport.

    We override the default client post-init because ``SharedFetcher.__post_init__``
    always creates a real one. Tests also get a near-infinite rate limit to
    avoid sleeping in the happy path.
    """
    clock = {"t": 0.0}
    slept: list[float] = []

    def now() -> float:
        return clock["t"]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds

    fetcher = SharedFetcher(
        settings=settings,
        rate_per_sec=rate_per_sec,
        _now=now,
        _sleep=sleep,
    )
    fetcher._client = httpx.Client(
        transport=transport,
        timeout=5.0,
        headers={"User-Agent": fetcher.user_agent},
    )
    fetcher._slept = slept  # type: ignore[attr-defined]  # test-only
    return fetcher


class TestAllowlist:
    def test_empty_allowlist_allows_everything(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            # robots.txt or the page itself — both 200 empty.
            return httpx.Response(200, text="")

        settings = _settings(tmp_path, allowed=[])
        fetcher = _fetcher_with_transport(settings, httpx.MockTransport(handler))
        response = fetcher.get("https://example.com/page")
        assert response.status_code == 200

    def test_allowlist_rejects_out_of_list_domain(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path, allowed=["wikipedia.org"])
        fetcher = _fetcher_with_transport(
            settings, httpx.MockTransport(lambda r: httpx.Response(200))
        )
        with pytest.raises(FetchBlockedError):
            fetcher.get("https://evil.example.com/page")

    def test_allowlist_allows_suffix_match(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="")

        settings = _settings(tmp_path, allowed=["wikipedia.org"])
        fetcher = _fetcher_with_transport(settings, httpx.MockTransport(handler))
        response = fetcher.get("https://en.wikipedia.org/wiki/Photosynthesis")
        assert response.status_code == 200


class TestRobotsTxt:
    def test_robots_disallowed_raises(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200, text="User-agent: *\nDisallow: /private/"
                )
            return httpx.Response(200, text="")

        settings = _settings(tmp_path)
        fetcher = _fetcher_with_transport(settings, httpx.MockTransport(handler))
        with pytest.raises(FetchBlockedError, match="robots.txt disallows"):
            fetcher.get("https://example.com/private/secret")

    def test_robots_allowed_passes(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200, text="User-agent: *\nAllow: /public/"
                )
            return httpx.Response(200, text="ok")

        settings = _settings(tmp_path)
        fetcher = _fetcher_with_transport(settings, httpx.MockTransport(handler))
        response = fetcher.get("https://example.com/public/page")
        assert response.status_code == 200
        assert response.text == "ok"

    def test_missing_robots_txt_treated_as_allow(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, text="ok")

        settings = _settings(tmp_path)
        fetcher = _fetcher_with_transport(settings, httpx.MockTransport(handler))
        response = fetcher.get("https://example.com/any")
        assert response.status_code == 200

    def test_robots_cache_hits_only_once_per_host(self, tmp_path: Path) -> None:
        calls = {"robots": 0, "page": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                calls["robots"] += 1
                return httpx.Response(200, text="")
            calls["page"] += 1
            return httpx.Response(200, text="ok")

        settings = _settings(tmp_path)
        fetcher = _fetcher_with_transport(settings, httpx.MockTransport(handler))
        fetcher.get("https://example.com/a")
        fetcher.get("https://example.com/b")
        fetcher.get("https://example.com/c")
        assert calls["robots"] == 1  # cached after first lookup
        assert calls["page"] == 3


class TestBackoff:
    def test_429_retries_then_succeeds(self, tmp_path: Path) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="")
            attempts["n"] += 1
            if attempts["n"] < 3:
                return httpx.Response(429, text="slow down")
            return httpx.Response(200, text="ok")

        settings = _settings(tmp_path)
        fetcher = _fetcher_with_transport(settings, httpx.MockTransport(handler))
        response = fetcher.get("https://example.com/page")
        assert response.status_code == 200
        assert attempts["n"] == 3

    def test_permanent_4xx_raises_immediately(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="")
            return httpx.Response(404, text="nope")

        settings = _settings(tmp_path)
        fetcher = _fetcher_with_transport(settings, httpx.MockTransport(handler))
        with pytest.raises(FetchError, match="HTTP 404"):
            fetcher.get("https://example.com/missing")

    def test_gives_up_after_retry_budget(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="")
            return httpx.Response(503, text="down")

        settings = _settings(tmp_path)
        fetcher = _fetcher_with_transport(settings, httpx.MockTransport(handler))
        with pytest.raises(FetchTransientError):
            fetcher.get("https://example.com/page")


class TestRateLimit:
    def test_sleeps_between_requests_to_same_host(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="")

        settings = _settings(tmp_path)
        fetcher = _fetcher_with_transport(
            settings, httpx.MockTransport(handler), rate_per_sec=2.0
        )
        fetcher.get("https://example.com/a")
        fetcher.get("https://example.com/b")
        slept: list[float] = fetcher._slept  # type: ignore[attr-defined]
        # One sleep for the second call (≥ 0.5s gap at 2 req/sec).
        non_zero = [s for s in slept if s > 0]
        assert any(s >= 0.5 for s in non_zero)
