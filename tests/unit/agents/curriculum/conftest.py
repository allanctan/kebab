"""Fixtures for curriculum tests — a tiny synthetic XLSX in tmp_path."""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest


@pytest.fixture
def sample_xlsx(tmp_path: Path) -> Path:
    """Synthesize a 5-row MATATAG-shaped XLSX for parser tests."""
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "All Learning Objectives"
    ws.append(
        [
            "LC Code",
            "Curriculum",
            "Subject",
            "Grade Level",
            "Key Stage",
            "Quarter",
            "Domain",
            "Subdomain",
            "Learning Competency",
            "Content Standard",
            "Performance Standard",
            "Source PDF",
        ]
    )
    ws.append(
        [
            "SCI10-PT-I-1",
            "MATATAG",
            "Science",
            "10",
            "KS4",
            "Q1",
            "Plate Tectonics",
            "Plate Boundaries",
            "Describe the three types of plate boundaries.",
            "Learners demonstrate understanding of plate tectonics.",
            "Learners can explain plate boundary types.",
            "https://example.com/sci-cg.pdf",
        ]
    )
    ws.append(
        [
            "SCI10-PT-I-2",
            "MATATAG",
            "Science",
            "10",
            "KS4",
            "Q1",
            "Plate Tectonics",
            "Evidence",
            "Cite evidence supporting plate movement.",
            "Learners demonstrate understanding of plate tectonics.",
            "Learners can explain plate boundary types.",
            "https://example.com/sci-cg.pdf",
        ]
    )
    ws.append(
        [
            "EN10LIT-I-1",
            "MATATAG",
            "English",
            "10",
            "KS4",
            "Q1",
            "Literary Text",
            None,
            "Analyze drama as expressions of communal values.",
            None,
            None,
            None,
        ]
    )
    # G9 row — should be filtered out when grade_filter='10'
    ws.append(
        [
            "MA9NS-I-1",
            "MATATAG",
            "Mathematics",
            "9",
            "KS3",
            "Q1",
            "Number Sense",
            None,
            "Apply operations on radicals.",
            None,
            None,
            None,
        ]
    )
    # Garbage spacer row — blank code, must be dropped
    ws.append([None, None, None, None, None, None, None, None, None, None, None, None])

    out = tmp_path / "sample.xlsx"
    wb.save(out)
    return out
