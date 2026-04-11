"""Tests for figure manifest loading, validation, and resolution."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.images.figures import (
    FigureEntry,
    FigureManifest,
    copy_figures,
    load_figure_manifest,
    resolve_figure_markers,
)


class TestLoadFigureManifest:
    def test_loads_useful_figures(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed" / "documents" / "test_doc"
        figures_dir = processed / "figures"
        figures_dir.mkdir(parents=True)
        (figures_dir / "p001_f02.jpeg").write_bytes(b"fake image")
        figures_json = processed / "figures.json"
        figures_json.write_text(json.dumps([
            {"page": 1, "index": 2, "path": "figures/p001_f02.jpeg",
             "description": "Diagram of plates", "skip_reason": "",
             "width": 500, "height": 400, "mime_type": "image/jpeg"},
            {"page": 1, "index": 3, "path": "",
             "description": "DECORATIVE", "skip_reason": "tiny",
             "width": 10, "height": 10, "mime_type": "image/png"},
        ]))
        manifest = load_figure_manifest(processed)
        assert len(manifest.entries) == 1
        assert manifest.entries[0].figure_id == "p001_f02"
        assert manifest.entries[0].description == "Diagram of plates"

    def test_skips_error_descriptions(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed" / "documents" / "test_doc"
        figures_dir = processed / "figures"
        figures_dir.mkdir(parents=True)
        (figures_dir / "p001_f01.jpeg").write_bytes(b"fake")
        figures_json = processed / "figures.json"
        figures_json.write_text(json.dumps([
            {"page": 1, "index": 1, "path": "figures/p001_f01.jpeg",
             "description": "ERROR: API failed", "skip_reason": "describer_error",
             "width": 500, "height": 400, "mime_type": "image/jpeg"},
        ]))
        manifest = load_figure_manifest(processed)
        assert len(manifest.entries) == 0

    def test_empty_when_no_figures_json(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed" / "documents" / "test_doc"
        processed.mkdir(parents=True)
        manifest = load_figure_manifest(processed)
        assert len(manifest.entries) == 0

    def test_prompt_text(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed" / "documents" / "test_doc"
        figures_dir = processed / "figures"
        figures_dir.mkdir(parents=True)
        (figures_dir / "p001_f02.jpeg").write_bytes(b"fake")
        figures_json = processed / "figures.json"
        figures_json.write_text(json.dumps([
            {"page": 1, "index": 2, "path": "figures/p001_f02.jpeg",
             "description": "Plate diagram", "skip_reason": "",
             "width": 500, "height": 400, "mime_type": "image/jpeg"},
        ]))
        manifest = load_figure_manifest(processed)
        text = manifest.prompt_text()
        assert "[1] p001_f02" in text
        assert "Plate diagram" in text

    def test_empty_prompt_text(self) -> None:
        manifest = FigureManifest()
        assert manifest.prompt_text() == ""

    def test_skips_records_with_skip_reason(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed" / "documents" / "test_doc"
        figures_dir = processed / "figures"
        figures_dir.mkdir(parents=True)
        (figures_dir / "p002_f01.jpeg").write_bytes(b"fake")
        figures_json = processed / "figures.json"
        figures_json.write_text(json.dumps([
            {"page": 2, "index": 1, "path": "figures/p002_f01.jpeg",
             "description": "A real figure", "skip_reason": "repeated",
             "width": 500, "height": 400, "mime_type": "image/jpeg"},
        ]))
        manifest = load_figure_manifest(processed)
        assert len(manifest.entries) == 0

    def test_skips_missing_file_on_disk(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed" / "documents" / "test_doc"
        processed.mkdir(parents=True)
        figures_json = processed / "figures.json"
        figures_json.write_text(json.dumps([
            {"page": 1, "index": 1, "path": "figures/ghost.jpeg",
             "description": "A figure", "skip_reason": "",
             "width": 500, "height": 400, "mime_type": "image/jpeg"},
        ]))
        manifest = load_figure_manifest(processed)
        assert len(manifest.entries) == 0

    def test_assigns_sequential_local_nums(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed" / "documents" / "test_doc"
        figures_dir = processed / "figures"
        figures_dir.mkdir(parents=True)
        for name in ("p001_f01.jpeg", "p002_f01.jpeg", "p003_f01.jpeg"):
            (figures_dir / name).write_bytes(b"fake")
        figures_json = processed / "figures.json"
        figures_json.write_text(json.dumps([
            {"page": 1, "index": 1, "path": "figures/p001_f01.jpeg",
             "description": "First", "skip_reason": "", "mime_type": "image/jpeg"},
            {"page": 2, "index": 1, "path": "figures/p002_f01.jpeg",
             "description": "Second", "skip_reason": "", "mime_type": "image/jpeg"},
            {"page": 3, "index": 1, "path": "figures/p003_f01.jpeg",
             "description": "Third", "skip_reason": "", "mime_type": "image/jpeg"},
        ]))
        manifest = load_figure_manifest(processed)
        assert [e.local_num for e in manifest.entries] == [1, 2, 3]

    def test_mime_type_from_record(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed" / "documents" / "test_doc"
        figures_dir = processed / "figures"
        figures_dir.mkdir(parents=True)
        (figures_dir / "p001_f01.png").write_bytes(b"fake")
        figures_json = processed / "figures.json"
        figures_json.write_text(json.dumps([
            {"page": 1, "index": 1, "path": "figures/p001_f01.png",
             "description": "PNG figure", "skip_reason": "", "mime_type": "image/png"},
        ]))
        manifest = load_figure_manifest(processed)
        assert manifest.entries[0].mime_type == "image/png"

    def test_mime_type_defaults_to_jpeg(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed" / "documents" / "test_doc"
        figures_dir = processed / "figures"
        figures_dir.mkdir(parents=True)
        (figures_dir / "p001_f01.jpeg").write_bytes(b"fake")
        figures_json = processed / "figures.json"
        figures_json.write_text(json.dumps([
            {"page": 1, "index": 1, "path": "figures/p001_f01.jpeg",
             "description": "JPEG figure", "skip_reason": ""},
        ]))
        manifest = load_figure_manifest(processed)
        assert manifest.entries[0].mime_type == "image/jpeg"


class TestFigureManifestGet:
    def _manifest(self) -> FigureManifest:
        return FigureManifest(entries=[
            FigureEntry(local_num=1, figure_id="p001_f01", description="First",
                        source_path=Path("/tmp/p001_f01.jpeg"), mime_type="image/jpeg"),
            FigureEntry(local_num=2, figure_id="p002_f01", description="Second",
                        source_path=Path("/tmp/p002_f01.jpeg"), mime_type="image/jpeg"),
        ])

    def test_get_returns_correct_entry(self) -> None:
        manifest = self._manifest()
        entry = manifest.get(1)
        assert entry is not None
        assert entry.figure_id == "p001_f01"

    def test_get_returns_none_for_missing_num(self) -> None:
        manifest = self._manifest()
        assert manifest.get(99) is None

    def test_get_returns_correct_entry_for_second(self) -> None:
        manifest = self._manifest()
        entry = manifest.get(2)
        assert entry is not None
        assert entry.figure_id == "p002_f01"


class TestResolveFigureMarkers:
    def test_resolves_valid_markers(self) -> None:
        manifest = FigureManifest(entries=[
            FigureEntry(local_num=1, figure_id="p001_f02", description="Plates diagram",
                        source_path=Path("/tmp/figures/p001_f02.jpeg"), mime_type="image/jpeg"),
            FigureEntry(local_num=2, figure_id="p003_f01", description="Map",
                        source_path=Path("/tmp/figures/p003_f01.jpeg"), mime_type="image/jpeg"),
        ])
        body = "Intro text.\n\n[FIGURE:1]\n\nMore text.\n\n[FIGURE:2]\n"
        result, used = resolve_figure_markers(body, manifest, "my-article")
        assert "[FIGURE:1]" not in result
        assert "[FIGURE:2]" not in result
        assert "![Plates diagram]" in result
        assert "figures/my-article/p001_f02.jpeg" in result
        assert len(used) == 2

    def test_strips_invalid_markers(self) -> None:
        manifest = FigureManifest(entries=[
            FigureEntry(local_num=1, figure_id="p001_f02", description="Plates",
                        source_path=Path("/tmp/figures/p001_f02.jpeg"), mime_type="image/jpeg"),
        ])
        body = "Text.\n\n[FIGURE:1]\n\n[FIGURE:99]\n"
        result, used = resolve_figure_markers(body, manifest, "slug")
        assert "[FIGURE:99]" not in result
        assert len(used) == 1

    def test_no_markers_returns_unchanged(self) -> None:
        manifest = FigureManifest(entries=[])
        body = "Just text, no figures.\n"
        result, used = resolve_figure_markers(body, manifest, "slug")
        assert result == body
        assert len(used) == 0

    def test_image_markdown_uses_correct_extension(self) -> None:
        manifest = FigureManifest(entries=[
            FigureEntry(local_num=1, figure_id="p001_f01", description="PNG img",
                        source_path=Path("/tmp/figures/p001_f01.png"), mime_type="image/png"),
        ])
        body = "[FIGURE:1]"
        result, used = resolve_figure_markers(body, manifest, "my-slug")
        assert "p001_f01.png" in result
        assert len(used) == 1

    def test_duplicate_markers_include_entry_twice(self) -> None:
        manifest = FigureManifest(entries=[
            FigureEntry(local_num=1, figure_id="p001_f01", description="Chart",
                        source_path=Path("/tmp/p001_f01.jpeg"), mime_type="image/jpeg"),
        ])
        body = "[FIGURE:1] and again [FIGURE:1]"
        result, used = resolve_figure_markers(body, manifest, "slug")
        assert result.count("![Chart]") == 2
        assert len(used) == 2

    def test_all_invalid_markers_stripped(self) -> None:
        manifest = FigureManifest(entries=[])
        body = "[FIGURE:1][FIGURE:2][FIGURE:3]"
        result, used = resolve_figure_markers(body, manifest, "slug")
        assert result == ""
        assert len(used) == 0


class TestCopyFigures:
    def test_copies_used_figures(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "figures"
        src.mkdir(parents=True)
        (src / "p001_f02.jpeg").write_bytes(b"image data")
        dest = tmp_path / "curated" / "figures" / "my-article"

        entries = [
            FigureEntry(local_num=1, figure_id="p001_f02", description="test",
                        source_path=src / "p001_f02.jpeg", mime_type="image/jpeg"),
        ]
        copy_figures(entries, dest)
        assert (dest / "p001_f02.jpeg").exists()
        assert (dest / "p001_f02.jpeg").read_bytes() == b"image data"

    def test_creates_dest_dir(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "img.jpeg").write_bytes(b"data")
        dest = tmp_path / "new" / "dir"
        copy_figures(
            [FigureEntry(local_num=1, figure_id="img", description="x",
                         source_path=src / "img.jpeg", mime_type="image/jpeg")],
            dest,
        )
        assert dest.is_dir()

    def test_empty_entries_noop(self, tmp_path: Path) -> None:
        dest = tmp_path / "should_not_exist"
        copy_figures([], dest)
        assert not dest.exists()

    def test_copies_multiple_figures(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        for name in ("a.jpeg", "b.jpeg"):
            (src / name).write_bytes(b"data")
        dest = tmp_path / "dest"
        copy_figures(
            [
                FigureEntry(local_num=1, figure_id="a", description="A",
                            source_path=src / "a.jpeg", mime_type="image/jpeg"),
                FigureEntry(local_num=2, figure_id="b", description="B",
                            source_path=src / "b.jpeg", mime_type="image/jpeg"),
            ],
            dest,
        )
        assert (dest / "a.jpeg").exists()
        assert (dest / "b.jpeg").exists()

    def test_skips_missing_source_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        dest.mkdir()
        entries = [
            FigureEntry(local_num=1, figure_id="ghost", description="missing",
                        source_path=tmp_path / "nonexistent.jpeg", mime_type="image/jpeg"),
        ]
        # Should not raise, just log a warning
        copy_figures(entries, dest)
        assert not (dest / "ghost.jpeg").exists()
