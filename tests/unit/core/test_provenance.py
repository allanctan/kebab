"""Provenance sidecar I/O + checksum helpers."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.sources.provenance import (
    find_by_checksum,
    read_sidecar,
    sha256_bytes,
    sha256_file,
    sidecar_path,
    write_sidecar,
)
from app.core.sources.adapter import FetchedArtifact
from app.models.source import Source


def _artifact(raw_path: Path, checksum: str = "deadbeef") -> FetchedArtifact:
    return FetchedArtifact(
        raw_path=raw_path,
        source=Source(
            id=0,
            title="Example",
            tier=2,
            adapter="stub",
            checksum=checksum,
        ),
        content_hash=checksum,
        license="CC-BY-4.0",
    )


class TestChecksums:
    def test_sha256_bytes_is_stable(self) -> None:
        assert sha256_bytes(b"hello") == sha256_bytes(b"hello")
        assert sha256_bytes(b"hello") != sha256_bytes(b"hellO")

    def test_sha256_file_matches_bytes(self, tmp_path: Path) -> None:
        data = b"some content"
        path = tmp_path / "x.bin"
        path.write_bytes(data)
        assert sha256_file(path) == sha256_bytes(data)

    def test_sha256_file_streams_large_input(self, tmp_path: Path) -> None:
        # 256 KiB — larger than the 64 KiB chunk size to exercise the loop.
        data = b"A" * (256 * 1024)
        path = tmp_path / "big.bin"
        path.write_bytes(data)
        assert sha256_file(path) == sha256_bytes(data)


class TestSidecarRoundTrip:
    def test_write_then_read(self, tmp_path: Path) -> None:
        raw = tmp_path / "docs" / "x.pdf"
        raw.parent.mkdir()
        raw.write_bytes(b"bytes")
        artifact = _artifact(raw)
        sidecar = write_sidecar(artifact)
        assert sidecar == sidecar_path(raw)
        assert sidecar.exists()

        loaded = read_sidecar(raw)
        assert loaded is not None
        assert loaded.content_hash == "deadbeef"
        assert loaded.source.title == "Example"
        assert loaded.license == "CC-BY-4.0"

    def test_read_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert read_sidecar(tmp_path / "nope.pdf") is None

    def test_read_returns_none_on_invalid_json(self, tmp_path: Path) -> None:
        raw = tmp_path / "x.pdf"
        raw.write_bytes(b"x")
        sidecar_path(raw).write_text("{not valid json", encoding="utf-8")
        assert read_sidecar(raw) is None

    def test_read_returns_none_on_schema_violation(self, tmp_path: Path) -> None:
        raw = tmp_path / "x.pdf"
        raw.write_bytes(b"x")
        sidecar_path(raw).write_text(json.dumps({"oops": 1}), encoding="utf-8")
        assert read_sidecar(raw) is None


class TestFindByChecksum:
    def test_finds_matching_artifact(self, tmp_path: Path) -> None:
        raw = tmp_path / "sub" / "x.pdf"
        raw.parent.mkdir()
        raw.write_bytes(b"bytes")
        write_sidecar(_artifact(raw, checksum="ab12"))

        found = find_by_checksum(tmp_path, "ab12")
        assert found == raw

    def test_returns_none_on_no_match(self, tmp_path: Path) -> None:
        raw = tmp_path / "x.pdf"
        raw.write_bytes(b"x")
        write_sidecar(_artifact(raw, checksum="ab12"))
        assert find_by_checksum(tmp_path, "ffff") is None

    def test_returns_none_when_dir_missing(self, tmp_path: Path) -> None:
        assert find_by_checksum(tmp_path / "missing", "x") is None
