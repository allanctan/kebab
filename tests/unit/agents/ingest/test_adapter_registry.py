"""Adapter registry — lookup, conformance, default wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.config import Settings
from app.core.errors import KebabError
from app.core.sources.adapter import Candidate, FetchedArtifact, SourceAdapter
from app.agents.ingest.registry import AdapterRegistry, build_default_registry


class _StubAdapter:
    name = "stub_test"
    default_tier = 2

    def discover(self, query: str, *, limit: int = 10) -> list[Candidate]:
        return []

    def fetch(self, candidate: Candidate) -> FetchedArtifact:
        raise NotImplementedError


class _BrokenAdapter:
    """Not a SourceAdapter — missing required attributes."""

    name = "broken"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        KNOWLEDGE_DIR=tmp_path,
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )


class TestAdapterRegistry:
    def test_register_and_get_round_trip(self, tmp_path: Path) -> None:
        registry = AdapterRegistry(settings=_settings(tmp_path))
        adapter = _StubAdapter()
        registry.register(adapter)
        assert registry.get("stub_test") is adapter
        assert "stub_test" in registry.names()

    def test_get_missing_raises_kebab_error(self, tmp_path: Path) -> None:
        registry = AdapterRegistry(settings=_settings(tmp_path))
        with pytest.raises(KebabError, match="no adapter"):
            registry.get("nope")

    def test_register_rejects_non_protocol(self, tmp_path: Path) -> None:
        registry = AdapterRegistry(settings=_settings(tmp_path))
        with pytest.raises(KebabError, match="SourceAdapter"):
            registry.register(_BrokenAdapter())  # type: ignore[arg-type]

    def test_register_overwrites_silently(self, tmp_path: Path) -> None:
        registry = AdapterRegistry(settings=_settings(tmp_path))
        first = _StubAdapter()
        second = _StubAdapter()
        registry.register(first)
        registry.register(second)
        assert registry.get("stub_test") is second


class TestDefaultRegistry:
    def test_default_registry_has_all_builtin_adapters(self, tmp_path: Path) -> None:
        registry = build_default_registry(_settings(tmp_path))
        assert set(registry.names()) == {"local_pdf", "direct_url", "tavily", "wikipedia", "openstax"}

    def test_default_adapters_are_source_adapters(self, tmp_path: Path) -> None:
        registry = build_default_registry(_settings(tmp_path))
        for name in registry.names():
            assert isinstance(registry.get(name), SourceAdapter)
