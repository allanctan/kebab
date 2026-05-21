"""Parser tests — synthetic MATATAG-shaped XLSX."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.curriculum.parser import parse_xlsx


class TestParseXlsx:
    def test_returns_all_rows_when_no_grade_filter(self, sample_xlsx: Path) -> None:
        comps = parse_xlsx(sample_xlsx)
        codes = [c.code for c in comps]
        assert codes == ["SCI10-PT-I-1", "SCI10-PT-I-2", "EN10LIT-I-1", "MA9NS-I-1"]

    def test_grade_filter_keeps_only_matching_grade(self, sample_xlsx: Path) -> None:
        comps = parse_xlsx(sample_xlsx, grade_filter="10")
        codes = [c.code for c in comps]
        assert codes == ["SCI10-PT-I-1", "SCI10-PT-I-2", "EN10LIT-I-1"]
        assert all(c.grade == "10" for c in comps)

    def test_drops_blank_code_rows(self, sample_xlsx: Path) -> None:
        comps = parse_xlsx(sample_xlsx)
        # The trailing all-None row should be excluded; only 4 real rows remain.
        assert len(comps) == 4

    def test_populates_optional_fields_when_present(self, sample_xlsx: Path) -> None:
        comps = parse_xlsx(sample_xlsx, grade_filter="10")
        sci = next(c for c in comps if c.code == "SCI10-PT-I-1")
        assert sci.subject == "Science"
        assert sci.domain == "Plate Tectonics"
        assert sci.subdomain == "Plate Boundaries"
        assert sci.source_pdf_url == "https://example.com/sci-cg.pdf"
        assert sci.content_standard is not None
        assert "plate tectonics" in sci.content_standard.lower()

    def test_handles_missing_optional_fields(self, sample_xlsx: Path) -> None:
        comps = parse_xlsx(sample_xlsx)
        eng = next(c for c in comps if c.code == "EN10LIT-I-1")
        # subdomain, content_standard, performance_standard, source_pdf_url all None
        assert eng.subdomain in (None, "")
        assert eng.content_standard in (None, "")
        assert eng.performance_standard in (None, "")
        assert eng.source_pdf_url in (None, "")

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_xlsx(tmp_path / "nope.xlsx")

    def test_raises_on_missing_sheet(self, sample_xlsx: Path) -> None:
        with pytest.raises(KeyError, match="sheet"):
            parse_xlsx(sample_xlsx, sheet_name="DoesNotExist")
