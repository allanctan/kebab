"""Source adapter protocol shape and model validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.sources.adapter import (
    AdapterError,
    Candidate,
    FetchedArtifact,
    SourceAdapter,
)
from app.models.source import Source


class _StubAdapter:
    """Minimal adapter used to assert runtime protocol conformance."""

    name = "stub"
    default_tier: int = 3

    def discover(self, query: str, *, limit: int = 10) -> list[Candidate]:
        return []

    def fetch(self, candidate: Candidate) -> FetchedArtifact:
        raise AdapterError("stub cannot fetch")


class TestCandidate:
    def test_requires_all_fields(self) -> None:
        cand = Candidate(
            adapter="stub",
            locator="file:///tmp/x.pdf",
            title="X",
            tier_hint=2,
        )
        assert cand.snippet is None
        assert cand.tier_hint == 2

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            Candidate.model_validate(
                {
                    "adapter": "stub",
                    "locator": "x",
                    "title": "x",
                    "tier_hint": 2,
                    "extra": "nope",  # extra="forbid"
                }
            )


class TestFetchedArtifact:
    def test_round_trip(self, tmp_path: Path) -> None:
        raw = tmp_path / "x.pdf"
        raw.write_bytes(b"pdfbytes")
        source = Source(id=0, title="X", tier=2, checksum="deadbeef", adapter="stub")
        artifact = FetchedArtifact(
            raw_path=raw,
            source=source,
            content_hash="deadbeef",
            license="CC-BY-4.0",
        )
        dumped = artifact.model_dump(mode="json")
        reloaded = FetchedArtifact.model_validate(dumped)
        assert reloaded.raw_path == raw
        assert reloaded.license == "CC-BY-4.0"
        assert reloaded.source.tier == 2

    def test_source_tier_required(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError):
            FetchedArtifact(
                raw_path=tmp_path / "x",
                source=Source.model_validate({"title": "X"}),  # missing tier
                content_hash="x",
            )


class TestProtocolConformance:
    def test_stub_adapter_is_recognized_as_source_adapter(self) -> None:
        adapter = _StubAdapter()
        # runtime_checkable lets isinstance work against the Protocol.
        assert isinstance(adapter, SourceAdapter)

    def test_non_adapter_rejected_by_isinstance(self) -> None:
        class _Broken:
            name = "broken"
            # Missing default_tier, discover, fetch.

        assert not isinstance(_Broken(), SourceAdapter)
