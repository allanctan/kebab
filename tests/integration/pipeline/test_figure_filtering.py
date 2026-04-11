"""Integration test for figure filtering on real DepEd PDFs.

Extracts figures from fixture PDFs and runs the pre-LLM filter pipeline.
No LLM calls.

Run with -s to see detailed report:
    uv run pytest tests/integration/pipeline/test_figure_filtering.py -v -s
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.config import Settings
from app.core.images.filter_images import build_hash_page_counts, decide
from app.utils.pdf_extractor import extract


_FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "pdf"

_PDFS = [
    "SCI9-Q4-MOD1-Projectile Motion.pdf",
    "SCI10_Q1_M3_Processes and Landforms along plate boundaries.pdf",
    "MATH_GR10_QTR1-MODULE-2edited_FORMATTED_12PAGES (1).pdf",
    "NCR_FINAL_Q1_ENG10_M1-Val.pdf",
]

# Expected kept counts per PDF after pre-LLM filtering (FIGURE_MIN_REL_AREA=1.5%).
# Verified by manual review.
_EXPECTED_KEPT = {
    "SCI9-Q4-MOD1-Projectile Motion.pdf": 23,
    "SCI10_Q1_M3_Processes and Landforms along plate boundaries.pdf": 26,
    "MATH_GR10_QTR1-MODULE-2edited_FORMATTED_12PAGES (1).pdf": 3,
    "NCR_FINAL_Q1_ENG10_M1-Val.pdf": 1,
}


@pytest.fixture(params=_PDFS)
def pdf_path(request: pytest.FixtureRequest) -> Path:
    path = _FIXTURES / request.param
    if not path.exists():
        pytest.skip(f"fixture PDF not found: {path.name}")
    return path


@pytest.mark.integration
def test_filter_kept_count(pdf_path: Path, tmp_path: Path) -> None:
    """Verify pre-LLM filter keeps the expected number of figures."""
    settings = Settings(
        KNOWLEDGE_DIR=tmp_path, RAW_DIR=tmp_path / "raw",
        QDRANT_PATH=None, QDRANT_URL=None, GOOGLE_API_KEY="x",
    )
    extraction = extract(pdf_path, extract_figures=True)
    hash_counts = build_hash_page_counts(extraction.figures)
    kept = sum(1 for fig in extraction.figures if decide(fig, hash_counts, settings).keep)
    expected = _EXPECTED_KEPT[pdf_path.name]
    assert kept == expected, (
        f"{pdf_path.name}: expected {expected} kept, got {kept}"
    )


@pytest.mark.integration
def test_filter_report(pdf_path: Path, tmp_path: Path) -> None:
    """Print per-figure filter decisions for manual review. Run with -s."""
    settings = Settings(
        KNOWLEDGE_DIR=tmp_path, RAW_DIR=tmp_path / "raw",
        QDRANT_PATH=None, QDRANT_URL=None, GOOGLE_API_KEY="x",
    )
    extraction = extract(pdf_path, extract_figures=True)
    hash_counts = build_hash_page_counts(extraction.figures)

    kept = 0
    dropped = 0
    reasons: dict[str, int] = {}

    print(f"\n{'='*70}")
    print(f"{pdf_path.name}")
    print(f"{'='*70}")

    for fig in extraction.figures:
        decision = decide(fig, hash_counts, settings)
        status = "DROP" if not decision.keep else "KEEP"
        reason = f" ({decision.reason})" if not decision.keep else ""

        if decision.keep:
            kept += 1
        else:
            dropped += 1
            reasons[decision.reason] = reasons.get(decision.reason, 0) + 1

        print(
            f"  [{status:4s}] p{fig.page:>2d}.{fig.index} "
            f"{fig.width:>5d}x{fig.height:<5d} "
            f"rel={fig.rel_area:.4f} "
            f"asp={fig.aspect:.1f} "
            f"col={fig.dominant_color_usage or 0:.2f} "
            f"hash_pg={hash_counts.get(fig.content_hash, 0)}"
            f"{reason}"
        )

    total = kept + dropped
    print(f"\n  {total} total, {kept} kept ({kept/total*100:.0f}%), "
          f"{dropped} dropped ({dropped/total*100:.0f}%)")
    print(f"  reasons: {reasons}")
