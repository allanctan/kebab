"""PDF extraction: text only and text+figures."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from app.utils.pdf_extractor import extract, extract_text


def _tiny_png() -> bytes:
    """Build a valid 32x32 red PNG via PyMuPDF itself (no hardcoded bytes)."""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 32, 32), 0)
    pixmap.set_rect(pixmap.irect, (255, 0, 0))
    return pixmap.tobytes("png")


def _build_minimal_pdf(path: Path, text: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def _build_pdf_with_image(path: Path, text: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    rect = pymupdf.Rect(100, 200, 300, 400)
    page.insert_image(rect, stream=_tiny_png())
    doc.save(path)
    doc.close()


def test_extract_text_returns_page_content(tmp_path: Path) -> None:
    pdf = tmp_path / "minimal.pdf"
    _build_minimal_pdf(pdf, "Hello KEBAB")
    assert "Hello KEBAB" in extract_text(pdf)


def test_extract_returns_pages_and_figures(tmp_path: Path) -> None:
    pdf = tmp_path / "with_figure.pdf"
    _build_pdf_with_image(pdf, "Body text")
    extraction = extract(pdf)
    assert len(extraction.pages) == 1
    assert "Body text" in extraction.pages[0].text
    assert len(extraction.figures) >= 1
    fig = extraction.figures[0]
    assert fig.page == 1
    assert fig.mime_type.startswith("image/")
    assert len(fig.bytes) > 0


def test_extract_skips_figures_when_disabled(tmp_path: Path) -> None:
    pdf = tmp_path / "with_figure.pdf"
    _build_pdf_with_image(pdf, "Body text")
    extraction = extract(pdf, extract_figures=False)
    assert extraction.figures == []
