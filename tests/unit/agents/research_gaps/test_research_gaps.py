"""Tests for app.agents.research_gaps.research_gaps helpers."""

from __future__ import annotations

from app.agents.research_gaps.research_gaps import _is_blocked_domain


class TestIsBlockedDomain:
    def test_brainly_is_blocked(self) -> None:
        assert _is_blocked_domain("https://brainly.com/question/31440523")

    def test_www_prefix_stripped(self) -> None:
        assert _is_blocked_domain("https://www.brainly.com/question/1")

    def test_fiveable_is_blocked(self) -> None:
        assert _is_blocked_domain("https://fiveable.me/some-topic")

    def test_quora_is_blocked(self) -> None:
        assert _is_blocked_domain("https://www.quora.com/q")

    def test_reddit_is_blocked(self) -> None:
        assert _is_blocked_domain("https://reddit.com/r/science/x")

    def test_authoritative_source_not_blocked(self) -> None:
        assert not _is_blocked_domain("https://britannica.com/science/plate-tectonics")

    def test_wikipedia_not_blocked(self) -> None:
        assert not _is_blocked_domain("https://en.wikipedia.org/wiki/Plate_tectonics")

    def test_government_site_not_blocked(self) -> None:
        assert not _is_blocked_domain("https://www.usgs.gov/publications/x")

    def test_malformed_url_not_blocked(self) -> None:
        assert not _is_blocked_domain("not-a-url")
