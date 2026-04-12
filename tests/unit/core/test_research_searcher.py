"""Tests for app.core.research.searcher."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from app.core.research.searcher import SourceContent, search
from app.core.sources.adapter import Candidate, FetchedArtifact
from app.models.source import Source


# ---------------------------------------------------------------------------
# Stub adapters for monkeypatching the registry
# ---------------------------------------------------------------------------


@dataclass
class _StubAdapter:
    """Adapter that returns predetermined candidates and content from disk."""

    name: ClassVar[str] = "stub"
    default_tier: int = 3
    candidates: list[Candidate] | None = None
    raw_dir: Path | None = None

    def discover(self, query: str, *, limit: int = 10) -> list[Candidate]:
        return list(self.candidates or [])

    def fetch(self, candidate: Candidate) -> FetchedArtifact:
        assert self.raw_dir is not None
        path = self.raw_dir / f"{candidate.locator}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"content for {candidate.title}".encode("utf-8"))
        return FetchedArtifact(
            raw_path=path,
            source=Source(id=1, title=candidate.title, tier=3),
            content_hash="deadbeef",
        )


@dataclass
class _ExplodingAdapter:
    """Adapter whose fetch always raises — used for failure-skip tests."""

    name: ClassVar[str] = "stub"
    default_tier: int = 3
    candidates: list[Candidate] | None = None

    def discover(self, query: str, *, limit: int = 10) -> list[Candidate]:
        return list(self.candidates or [])

    def fetch(self, candidate: Candidate) -> FetchedArtifact:
        raise RuntimeError("nope")


@dataclass
class _FakeRegistry:
    adapter: object
    known_names: tuple[str, ...] = ("stub",)

    def get(self, name: str) -> object:
        if name not in self.known_names:
            raise KeyError(name)
        return self.adapter

    def names(self) -> list[str]:
        return list(self.known_names)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> object:
    """Bare-minimum settings stand-in — searcher only reads KNOWLEDGE_DIR."""

    class _S:
        KNOWLEDGE_DIR = tmp_path / "knowledge"

    (_S.KNOWLEDGE_DIR / "raw" / "inbox").mkdir(parents=True)
    return _S()


def _candidate(locator: str, title: str) -> Candidate:
    return Candidate(adapter="stub", locator=locator, title=title, tier_hint=3)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSourceContent:
    def test_is_frozen_dataclass(self) -> None:
        sc = SourceContent(title="t", url="https://x", content="body")
        with pytest.raises(Exception):
            sc.title = "other"  # type: ignore[misc]


class TestSearch:
    def test_unknown_adapter_returns_empty(self, monkeypatch: pytest.MonkeyPatch, settings: object) -> None:
        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: _FakeRegistry(adapter=_StubAdapter()),
        )
        result = search(settings, "no-such-adapter", "x")
        assert result == []

    def test_returns_source_content_per_candidate(
        self, monkeypatch: pytest.MonkeyPatch, settings: object, tmp_path: Path
    ) -> None:
        adapter = _StubAdapter(
            candidates=[_candidate("a", "Title A"), _candidate("b", "Title B")],
            raw_dir=tmp_path / "raw",
        )
        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: _FakeRegistry(adapter=adapter),
        )
        result = search(settings, "stub", "query", limit=2)
        assert len(result) == 2
        assert result[0].title == "Title A"
        assert "Title A" in result[0].content
        assert result[1].title == "Title B"

    def test_wikipedia_url_built_from_locator(
        self, monkeypatch: pytest.MonkeyPatch, settings: object, tmp_path: Path
    ) -> None:
        wiki_candidate = Candidate(
            adapter="wikipedia",
            locator="Plate tectonics",
            title="Plate tectonics",
            tier_hint=4,
        )

        @dataclass
        class _WikiStub:
            name: ClassVar[str] = "wikipedia"
            default_tier: int = 4

            def discover(self, q: str, *, limit: int = 10) -> list[Candidate]:
                return [wiki_candidate]

            def fetch(self, c: Candidate) -> FetchedArtifact:
                p = tmp_path / "raw" / "wiki.txt"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"plate content")
                return FetchedArtifact(
                    raw_path=p,
                    source=Source(id=1, title=c.title, tier=4),
                    content_hash="abc",
                )

        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: _FakeRegistry(adapter=_WikiStub(), known_names=("wikipedia",)),
        )
        result = search(settings, "wikipedia", "plate tectonics")
        assert len(result) == 1
        assert result[0].url == "https://en.wikipedia.org/wiki/Plate%20tectonics"

    def test_fetch_failure_logs_and_skips(
        self, monkeypatch: pytest.MonkeyPatch, settings: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter = _ExplodingAdapter(candidates=[_candidate("a", "Title A")])
        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: _FakeRegistry(adapter=adapter),
        )
        with caplog.at_level("WARNING", logger="app.core.research.searcher"):
            result = search(settings, "stub", "query")
        assert result == []
        assert any("fetch failed" in r.message for r in caplog.records)

    def test_limit_caps_results(
        self, monkeypatch: pytest.MonkeyPatch, settings: object, tmp_path: Path
    ) -> None:
        adapter = _StubAdapter(
            candidates=[_candidate(f"c{i}", f"Title {i}") for i in range(5)],
            raw_dir=tmp_path / "raw",
        )
        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: _FakeRegistry(adapter=adapter),
        )
        result = search(settings, "stub", "query", limit=2)
        assert len(result) == 2

    def test_stages_to_inbox(
        self, monkeypatch: pytest.MonkeyPatch, settings: object, tmp_path: Path
    ) -> None:
        adapter = _StubAdapter(
            candidates=[_candidate("a", "Title A")],
            raw_dir=tmp_path / "raw",
        )
        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: _FakeRegistry(adapter=adapter),
        )
        search(settings, "stub", "query")
        inbox = settings.KNOWLEDGE_DIR / "raw" / "inbox"  # type: ignore[attr-defined]
        staged = list(inbox.iterdir())
        assert len(staged) == 1
        assert staged[0].name.startswith("research_")
