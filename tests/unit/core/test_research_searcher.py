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
    def test_unknown_adapter_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, settings: object
    ) -> None:
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
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings: object,
        caplog: pytest.LogCaptureFixture,
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


# ---------------------------------------------------------------------------
# Stub adapter that records discover() calls for assertion
# ---------------------------------------------------------------------------


@dataclass
class _RecordingTavilyAdapter:
    """Tavily-named adapter that records how discover() was called."""

    name: ClassVar[str] = "tavily"
    candidates: list[Candidate]
    raw_dir: Path
    default_tier: int = 4
    # Records of include_domains per discover() call
    discover_calls: list[list[str] | None] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.discover_calls is None:
            self.discover_calls = []

    def discover(
        self,
        query: str,
        *,
        limit: int = 10,
        include_domains: list[str] | None = None,
    ) -> list[Candidate]:
        self.discover_calls.append(include_domains)
        return list(self.candidates)

    def fetch(self, candidate: Candidate) -> FetchedArtifact:
        path = self.raw_dir / f"{candidate.locator.split('/')[-1]}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"content for {candidate.title}".encode("utf-8"))
        return FetchedArtifact(
            raw_path=path,
            source=Source(id=1, title=candidate.title, tier=4),
            content_hash="deadbeef",
        )


@dataclass
class _RecordingWikiAdapter:
    """Wikipedia-named adapter that records discover() calls."""

    name: ClassVar[str] = "wikipedia"
    candidates: list[Candidate]
    raw_dir: Path
    default_tier: int = 3
    discover_calls: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.discover_calls is None:
            self.discover_calls = []

    def discover(self, query: str, *, limit: int = 10) -> list[Candidate]:
        self.discover_calls.append(query)
        return list(self.candidates)

    def fetch(self, candidate: Candidate) -> FetchedArtifact:
        path = self.raw_dir / f"{candidate.locator}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"wiki content for {candidate.title}".encode("utf-8"))
        return FetchedArtifact(
            raw_path=path,
            source=Source(id=1, title=candidate.title, tier=3),
            content_hash="cafebabe",
        )


@dataclass
class _DualRegistry:
    """Registry holding a tavily and a wikipedia adapter keyed by name."""

    tavily: object
    wikipedia: object

    def get(self, name: str) -> object:
        if name == "tavily":
            return self.tavily
        if name == "wikipedia":
            return self.wikipedia
        raise KeyError(name)

    def names(self) -> list[str]:
        return ["tavily", "wikipedia"]


def _tavily_candidate(title: str = "Result") -> Candidate:
    return Candidate(
        adapter="tavily",
        locator=f"https://example.com/{title}",
        title=title,
        tier_hint=4,
    )


def _wiki_candidate(title: str = "WikiResult") -> Candidate:
    return Candidate(adapter="wikipedia", locator=title, title=title, tier_hint=3)


class TestAuthoritativeSourcePriority:
    """Tests for the authoritative-source fallback chain in search()."""

    def test_uses_tavily_include_domains_when_authoritative_sources_provided(
        self, monkeypatch: pytest.MonkeyPatch, settings: object, tmp_path: Path
    ) -> None:
        """discover() should be called with include_domains on the first attempt."""
        tavily = _RecordingTavilyAdapter(
            candidates=[_tavily_candidate("Auth Result")],
            raw_dir=tmp_path / "raw",
        )
        wiki = _RecordingWikiAdapter(candidates=[], raw_dir=tmp_path / "raw")
        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: _DualRegistry(tavily=tavily, wikipedia=wiki),
        )
        result = search(
            settings,
            "tavily",
            "plate tectonics",
            authoritative_sources=["britannica.com"],
        )
        assert len(result) == 1
        assert result[0].title == "Auth Result"
        assert tavily.discover_calls[0] == ["britannica.com"]

    def test_no_fallback_when_authoritative_returns_results(
        self, monkeypatch: pytest.MonkeyPatch, settings: object, tmp_path: Path
    ) -> None:
        """Wikipedia and general Tavily should NOT be called when step 1 succeeds."""
        tavily = _RecordingTavilyAdapter(
            candidates=[_tavily_candidate("Auth Result")],
            raw_dir=tmp_path / "raw",
        )
        wiki = _RecordingWikiAdapter(
            candidates=[_wiki_candidate()], raw_dir=tmp_path / "raw"
        )
        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: _DualRegistry(tavily=tavily, wikipedia=wiki),
        )
        search(settings, "tavily", "query", authoritative_sources=["britannica.com"])
        assert len(tavily.discover_calls) == 1
        assert len(wiki.discover_calls) == 0

    def test_falls_back_to_wikipedia_when_authoritative_returns_nothing(
        self, monkeypatch: pytest.MonkeyPatch, settings: object, tmp_path: Path
    ) -> None:
        """When step 1 returns nothing, Wikipedia should be tried next."""
        tavily = _RecordingTavilyAdapter(candidates=[], raw_dir=tmp_path / "raw")
        wiki = _RecordingWikiAdapter(
            candidates=[_wiki_candidate("Wiki Result")], raw_dir=tmp_path / "raw"
        )
        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: _DualRegistry(tavily=tavily, wikipedia=wiki),
        )
        result = search(
            settings, "tavily", "query", authoritative_sources=["britannica.com"]
        )
        assert len(result) == 1
        assert result[0].title == "Wiki Result"
        assert len(wiki.discover_calls) == 1
        # Tavily with include_domains was tried first, general tavily was NOT needed
        assert len(tavily.discover_calls) == 1

    def test_returns_empty_when_auth_and_wiki_return_nothing(
        self, monkeypatch: pytest.MonkeyPatch, settings: object, tmp_path: Path
    ) -> None:
        """When steps 1 and 2 return nothing, search returns [] — no general
        Tavily fallback. This keeps low-quality sources out of results."""
        tavily = _RecordingTavilyAdapter(candidates=[], raw_dir=tmp_path / "raw")
        wiki = _RecordingWikiAdapter(candidates=[], raw_dir=tmp_path / "raw")
        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: _DualRegistry(tavily=tavily, wikipedia=wiki),
        )
        result = search(
            settings, "tavily", "query", authoritative_sources=["britannica.com"]
        )
        assert result == []
        # Called once with include_domains — no second call without filter
        assert len(tavily.discover_calls) == 1
        assert tavily.discover_calls[0] == ["britannica.com"]
        assert len(wiki.discover_calls) == 1

    def test_no_authoritative_sources_uses_adapter_directly(
        self, monkeypatch: pytest.MonkeyPatch, settings: object, tmp_path: Path
    ) -> None:
        """Backward-compat: no authoritative_sources → single adapter call, no fallback."""
        tavily = _RecordingTavilyAdapter(
            candidates=[_tavily_candidate("Direct Result")],
            raw_dir=tmp_path / "raw",
        )
        wiki = _RecordingWikiAdapter(candidates=[], raw_dir=tmp_path / "raw")
        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: _DualRegistry(tavily=tavily, wikipedia=wiki),
        )
        result = search(settings, "tavily", "query")
        assert len(result) == 1
        assert result[0].title == "Direct Result"
        # Should have called discover once with no include_domains
        assert len(tavily.discover_calls) == 1
        assert tavily.discover_calls[0] is None
        assert len(wiki.discover_calls) == 0

    def test_empty_authoritative_sources_uses_adapter_directly(
        self, monkeypatch: pytest.MonkeyPatch, settings: object, tmp_path: Path
    ) -> None:
        """authoritative_sources=[] behaves the same as None — no fallback chain."""
        tavily = _RecordingTavilyAdapter(
            candidates=[_tavily_candidate("Direct Result")],
            raw_dir=tmp_path / "raw",
        )
        wiki = _RecordingWikiAdapter(candidates=[], raw_dir=tmp_path / "raw")
        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: _DualRegistry(tavily=tavily, wikipedia=wiki),
        )
        result = search(settings, "tavily", "query", authoritative_sources=[])
        assert len(result) == 1
        assert result[0].title == "Direct Result"
        # Empty list is falsy — should behave identically to None: single call, no fallback
        assert len(tavily.discover_calls) == 1
        assert tavily.discover_calls[0] is None
        assert len(wiki.discover_calls) == 0


class TestInboxCache:
    def test_uses_cached_content_instead_of_fetching(
        self, monkeypatch: pytest.MonkeyPatch, settings: object, tmp_path: Path
    ) -> None:
        """When inbox has a file for the URL, skip adapter.fetch and return cached content."""
        from app.core.research.searcher import _inbox_filename

        # Pre-populate the inbox cache
        knowledge_dir = tmp_path / "knowledge"
        inbox = knowledge_dir / "raw" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        locator = "https://britannica.com/science/plate-tectonics"
        filename = _inbox_filename("tavily", locator)
        (inbox / filename).write_text("cached page content")

        # Adapter that tracks whether fetch was called
        fetch_called: list[Candidate] = []
        adapter = _StubAdapter(
            candidates=[Candidate(
                adapter="stub",
                locator=locator,
                title="Plate Tectonics",
                tier_hint=4,
            )],
            raw_dir=tmp_path / "raw",
        )
        original_fetch = adapter.fetch

        def tracking_fetch(c: Candidate) -> FetchedArtifact:
            fetch_called.append(c)
            return original_fetch(c)

        adapter.fetch = tracking_fetch  # type: ignore[method-assign]

        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: type("R", (), {"get": lambda self, n: adapter})(),
        )
        results = search(settings, "stub", "plate tectonics")

        assert len(results) == 1
        assert results[0].content == "cached page content"
        assert fetch_called == [], "adapter.fetch should not be called when cache exists"

    def test_fetches_when_no_cache(
        self, monkeypatch: pytest.MonkeyPatch, settings: object, tmp_path: Path
    ) -> None:
        """When inbox has no matching file, normal fetch + stage occurs."""
        adapter = _StubAdapter(
            candidates=[_candidate("https://example.com/article", "Test")],
            raw_dir=tmp_path / "raw",
        )
        monkeypatch.setattr(
            "app.core.research.searcher.build_default_registry",
            lambda _s: type("R", (), {"get": lambda self, n: adapter})(),
        )
        results = search(settings, "stub", "query")

        assert len(results) == 1
        assert results[0].content == "content for Test"

    def test_inbox_filename_tavily(self) -> None:
        from app.core.research.searcher import _inbox_filename

        result = _inbox_filename("tavily", "https://britannica.com/science/plate-tectonics")
        assert result == "research_tavily_britannica-com-science-plate-tectonics.html"

    def test_inbox_filename_wikipedia(self) -> None:
        from app.core.research.searcher import _inbox_filename

        result = _inbox_filename("wikipedia", "Plate tectonics")
        assert result == "research_wikipedia_plate-tectonics.md"
