"""Coverage tests — spine + curated articles → reverse index."""

from __future__ import annotations

from pathlib import Path

from app.agents.curriculum.coverage import build_coverage, load_coverage
from app.agents.curriculum.curriculum import ingest_xlsx
from app.config.config import Settings


def _mk_settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    (knowledge / "curated" / "Knowledge" / "Science").mkdir(parents=True)
    return Settings(  # type: ignore[arg-type]
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        PROCESSED_DIR=knowledge / "processed",
        CURATED_DIR=knowledge / "curated",
    )


def _write_article(path: Path, article_id: str, codes: list[str]) -> None:
    """Write a minimal curated article with the given competency_codes."""
    body = f"""---
id: {article_id}
name: {article_id}
type: article
sources:
  - id: 1
    title: Test Source
    tier: 1
competency_codes: {codes}
---
# {article_id}

Body text.
"""
    path.write_text(body, encoding="utf-8")


class TestBuildCoverage:
    def test_reverse_index_maps_lc_to_articles(
        self, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        settings = _mk_settings(tmp_path)
        ingest_xlsx(
            settings, xlsx_path=sample_xlsx, name="test-spine", grade_filter="10"
        )
        # Write 2 articles that cover specific LCs
        curated = Path(settings.CURATED_DIR) / "Knowledge" / "Science"
        _write_article(
            curated / "plate-types.md",
            "SCI-001",
            ["SCI10-PT-I-1", "SCI10-PT-I-2"],
        )
        _write_article(
            curated / "plate-evidence.md",
            "SCI-002",
            ["SCI10-PT-I-2"],
        )

        coverage = build_coverage(settings, name="test-spine")
        assert coverage.total_competencies == 3
        assert coverage.covered == 2  # 2 of 3 LCs are covered
        assert coverage.uncovered == 1
        assert coverage.competencies["SCI10-PT-I-1"] == ["SCI-001"]
        # SCI-001 and SCI-002 both cover SCI10-PT-I-2 — sorted dedup
        assert coverage.competencies["SCI10-PT-I-2"] == ["SCI-001", "SCI-002"]
        # EN10LIT-I-1 has no articles — empty list (key still present)
        assert coverage.competencies["EN10LIT-I-1"] == []

    def test_by_subject_summary(self, sample_xlsx: Path, tmp_path: Path) -> None:
        settings = _mk_settings(tmp_path)
        ingest_xlsx(
            settings, xlsx_path=sample_xlsx, name="test-spine", grade_filter="10"
        )
        curated = Path(settings.CURATED_DIR) / "Knowledge" / "Science"
        _write_article(curated / "plate-types.md", "SCI-001", ["SCI10-PT-I-1"])

        coverage = build_coverage(settings, name="test-spine")
        assert coverage.by_subject["Science"].total == 2
        assert coverage.by_subject["Science"].covered == 1
        assert coverage.by_subject["Science"].uncovered == 1
        assert coverage.by_subject["English"].covered == 0

    def test_handles_no_curated_articles(
        self, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        settings = _mk_settings(tmp_path)
        ingest_xlsx(
            settings, xlsx_path=sample_xlsx, name="test-spine", grade_filter="10"
        )
        coverage = build_coverage(settings, name="test-spine")
        assert coverage.covered == 0
        assert coverage.uncovered == 3
        assert all(arts == [] for arts in coverage.competencies.values())

    def test_ignores_codes_outside_the_spine(
        self, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        """Articles tagged with codes from a sibling spine must not pollute coverage."""
        settings = _mk_settings(tmp_path)
        # Build G10 spine — fixture only has G10 rows, so G7 codes are outside.
        ingest_xlsx(
            settings, xlsx_path=sample_xlsx, name="g10-spine", grade_filter="10"
        )
        curated = Path(settings.CURATED_DIR) / "Knowledge" / "Science"
        # Article tags ONE valid G10 code plus a foreign G7 code
        _write_article(
            curated / "mixed.md",
            "SCI-001",
            ["SCI10-PT-I-1", "SCI7-ESS-I-2"],
        )

        coverage = build_coverage(settings, name="g10-spine")
        # Only the spine code is counted
        assert coverage.covered == 1
        assert "SCI10-PT-I-1" in coverage.competencies
        # Foreign code does NOT appear as a key
        assert "SCI7-ESS-I-2" not in coverage.competencies

    def test_load_coverage_round_trips(self, sample_xlsx: Path, tmp_path: Path) -> None:
        settings = _mk_settings(tmp_path)
        ingest_xlsx(
            settings, xlsx_path=sample_xlsx, name="test-spine", grade_filter="10"
        )
        written = build_coverage(settings, name="test-spine")
        loaded = load_coverage(settings, "test-spine")
        assert loaded.name == written.name
        assert loaded.total_competencies == written.total_competencies
        assert loaded.competencies == written.competencies
