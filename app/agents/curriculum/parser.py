"""XLSX → CurriculumSpine parser.

Reads a DepEd MATATAG-derived XLSX (sheet ``All Learning Objectives``)
and emits a :class:`~app.models.curriculum.CurriculumSpine`.

Tolerant of optional columns. Unknown columns are ignored. Rows with a
blank ``LC Code`` or blank ``Learning Competency`` are dropped — the
upstream data sometimes includes spacer/header rows.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import openpyxl

from app.models.curriculum import Competency

logger = logging.getLogger(__name__)


# Canonical column-header → field-name mapping. We lowercase + strip
# whitespace before matching so layout drift across DepEd revisions
# doesn't break the parser.
_COLUMN_MAP: dict[str, str] = {
    "lc code": "code",
    "curriculum": "curriculum",
    "subject": "subject",
    "grade level": "grade",
    "grade": "grade",
    "key stage": "key_stage",
    "quarter": "quarter",
    "domain": "domain",
    "subdomain": "subdomain",
    "learning competency": "competency",
    "content standard": "content_standard",
    "performance standard": "performance_standard",
    "source pdf": "source_pdf_url",
    "source pdf url": "source_pdf_url",
}

_REQUIRED_FIELDS: frozenset[str] = frozenset({"code", "competency"})


def _normalize_header(value: Any) -> str:
    """Lowercase and collapse whitespace for header matching."""
    return " ".join(str(value).strip().lower().split()) if value is not None else ""


def _cell_to_str(value: Any) -> str:
    """Convert a cell value to a stripped string. ``None`` → ``''``."""
    if value is None:
        return ""
    return str(value).strip()


def parse_xlsx(
    xlsx_path: Path,
    *,
    sheet_name: str = "All Learning Objectives",
    grade_filter: str | None = None,
) -> list[Competency]:
    """Parse a MATATAG-format XLSX into competencies.

    Args:
        xlsx_path:    Path to the source XLSX.
        sheet_name:   Worksheet to read. Defaults to MATATAG's standard sheet.
        grade_filter: If set, keep only competencies whose ``grade`` matches.

    Returns:
        A list of :class:`Competency` in source order.

    Raises:
        FileNotFoundError: if ``xlsx_path`` does not exist.
        KeyError: if the sheet is missing or required headers are absent.
    """
    if not xlsx_path.exists():
        raise FileNotFoundError(f"XLSX not found: {xlsx_path}")

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    if sheet_name not in wb.sheetnames:
        raise KeyError(
            f"sheet {sheet_name!r} not in workbook (have: {wb.sheetnames})"
        )

    ws = wb[sheet_name]
    rows = ws.iter_rows(values_only=True)
    header_row = next(rows, None)
    if header_row is None:
        return []

    # Build index map: column position → Competency field name
    field_by_col: dict[int, str] = {}
    for col_idx, header in enumerate(header_row):
        key = _normalize_header(header)
        if key in _COLUMN_MAP:
            field_by_col[col_idx] = _COLUMN_MAP[key]

    seen_fields = set(field_by_col.values())
    missing = _REQUIRED_FIELDS - seen_fields
    if missing:
        raise KeyError(
            f"required columns missing from {xlsx_path.name}: {sorted(missing)}"
        )

    competencies: list[Competency] = []
    for row_num, row in enumerate(rows, start=2):  # data starts at row 2
        record: dict[str, str] = {}
        for col_idx, field_name in field_by_col.items():
            if col_idx < len(row):
                record[field_name] = _cell_to_str(row[col_idx])

        # Skip rows missing required fields (e.g., trailing summary rows).
        if not record.get("code") or not record.get("competency"):
            continue

        # Apply grade filter before instantiating
        if grade_filter is not None and record.get("grade", "") != grade_filter:
            continue

        try:
            competencies.append(Competency.model_validate(record))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "curriculum parser: skipping row %d in %s — %s",
                row_num,
                xlsx_path.name,
                exc,
            )
            continue

    logger.info(
        "curriculum parser: parsed %d competencies from %s%s",
        len(competencies),
        xlsx_path.name,
        f" (grade={grade_filter})" if grade_filter else "",
    )
    return competencies


__all__ = ["parse_xlsx"]
