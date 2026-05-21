"""Ingest tests — XLSX → spine YAML round-trip."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.agents.curriculum.curriculum import ingest_xlsx, load_spine
from app.config.config import Settings


def _mk_settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir(parents=True)
    return Settings(  # type: ignore[arg-type]
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        PROCESSED_DIR=knowledge / "processed",
        CURATED_DIR=knowledge / "curated",
    )


class TestIngestXlsx:
    def test_writes_spine_yaml_with_expected_fields(
        self, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        settings = _mk_settings(tmp_path)
        result = ingest_xlsx(
            settings,
            xlsx_path=sample_xlsx,
            name="test-spine",
            grade_filter="10",
        )
        assert result.spine_path.exists()
        assert result.total_competencies == 3  # 3 G10 rows in fixture
        assert result.grade_filter == "10"

        loaded = yaml.safe_load(result.spine_path.read_text())
        assert loaded["name"] == "test-spine"
        assert loaded["curriculum"] == "MATATAG"
        assert loaded["grade_filter"] == "10"
        assert len(loaded["competencies"]) == 3

    def test_load_spine_round_trips(
        self, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        settings = _mk_settings(tmp_path)
        ingest_xlsx(
            settings, xlsx_path=sample_xlsx, name="test-spine", grade_filter="10"
        )
        spine = load_spine(settings, "test-spine")
        assert spine.name == "test-spine"
        assert {c.code for c in spine.competencies} == {
            "SCI10-PT-I-1",
            "SCI10-PT-I-2",
            "EN10LIT-I-1",
        }

    def test_source_file_recorded_relative_when_under_knowledge(
        self, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        # Move the fixture inside the knowledge tree
        settings = _mk_settings(tmp_path)
        dest = Path(settings.KNOWLEDGE_DIR) / "raw" / "curriculum" / "sample.xlsx"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(sample_xlsx.read_bytes())

        ingest_xlsx(settings, xlsx_path=dest, name="test-spine")
        spine = load_spine(settings, "test-spine")
        # Source file must be recorded relative to knowledge root
        assert not Path(spine.source_file).is_absolute()
        assert "sample.xlsx" in spine.source_file
