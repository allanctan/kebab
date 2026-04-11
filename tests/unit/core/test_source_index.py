"""Unit tests for app.core.source_index."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.sources.index import load_index, register_source, save_index


class TestSourceIndex:
    def test_load_index_returns_empty_when_file_missing(self, tmp_path: Path) -> None:
        index = load_index(tmp_path / ".kebab" / "sources.json")
        assert index.sources == []
        assert index.next_id == 1

    def test_register_source_assigns_sequential_id(self, tmp_path: Path) -> None:
        index_path = tmp_path / ".kebab" / "sources.json"
        index = load_index(index_path)
        entry = register_source(
            index,
            stem="SCI10_Q1_M1_Plate_Tectonics",
            raw_path="raw/documents/SCI10_Q1_M1_Plate Tectonics.pdf",
            title="SCI10 Q1 M1 Plate Tectonics",
            tier=1,
            checksum="abc123",
            adapter="local_pdf",
        )
        assert entry.id == 1
        assert index.next_id == 2

    def test_register_source_deduplicates_by_stem(self, tmp_path: Path) -> None:
        index_path = tmp_path / ".kebab" / "sources.json"
        index = load_index(index_path)
        entry1 = register_source(index, stem="SCI10_Q1_M1", raw_path="raw/documents/SCI10_Q1_M1.pdf", title="SCI10 Q1 M1", tier=1, checksum="abc", adapter="local_pdf")
        entry2 = register_source(index, stem="SCI10_Q1_M1", raw_path="raw/documents/SCI10_Q1_M1.pdf", title="SCI10 Q1 M1 Updated", tier=2, checksum="def", adapter="local_pdf")
        assert entry1.id == entry2.id == 1
        assert index.next_id == 2
        assert len(index.sources) == 1
        assert index.sources[0].title == "SCI10 Q1 M1 Updated"
        assert index.sources[0].checksum == "def"

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        index_path = tmp_path / ".kebab" / "sources.json"
        index = load_index(index_path)
        register_source(index, stem="test_stem", raw_path="raw/documents/test.pdf", title="Test", tier=1, checksum="aaa", adapter="local_pdf")
        save_index(index, index_path)
        reloaded = load_index(index_path)
        assert len(reloaded.sources) == 1
        assert reloaded.sources[0].id == 1
        assert reloaded.sources[0].stem == "test_stem"
        assert reloaded.next_id == 2

    def test_register_multiple_sources_sequential(self, tmp_path: Path) -> None:
        index_path = tmp_path / ".kebab" / "sources.json"
        index = load_index(index_path)
        e1 = register_source(index, stem="a", raw_path="a.pdf", title="A", tier=1, checksum="1", adapter="pdf")
        e2 = register_source(index, stem="b", raw_path="b.pdf", title="B", tier=1, checksum="2", adapter="pdf")
        e3 = register_source(index, stem="c", raw_path="c.pdf", title="C", tier=1, checksum="3", adapter="pdf")
        assert e1.id == 1
        assert e2.id == 2
        assert e3.id == 3
        assert index.next_id == 4

    def test_get_by_id(self, tmp_path: Path) -> None:
        index_path = tmp_path / ".kebab" / "sources.json"
        index = load_index(index_path)
        register_source(index, stem="a", raw_path="a.pdf", title="A", tier=1, checksum="1", adapter="pdf")
        register_source(index, stem="b", raw_path="b.pdf", title="B", tier=2, checksum="2", adapter="pdf")
        assert index.get(1).stem == "a"
        assert index.get(2).stem == "b"
        with pytest.raises(KeyError):
            index.get(99)

    def test_get_by_stem(self, tmp_path: Path) -> None:
        index_path = tmp_path / ".kebab" / "sources.json"
        index = load_index(index_path)
        register_source(index, stem="my_stem", raw_path="a.pdf", title="A", tier=1, checksum="1", adapter="pdf")
        assert index.get_by_stem("my_stem").id == 1
        assert index.get_by_stem("nonexistent") is None
