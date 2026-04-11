"""PDF text + figure extraction via PyMuPDF.

Pattern adapted from
``better-ed-ai/app/api/assessment/images/utils/extractor.py``. KEBAB
does sync extraction only — no async, no network.

The extractor returns a structured :class:`PdfExtraction` containing
per-page text and per-page figure metadata. A separate stage
(:mod:`app.pipeline.ingest.pdf`) calls the multimodal describer on
each figure and stitches descriptions into the final ``text.md``.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from app.core.errors import IngestError

logger = logging.getLogger(__name__)


@dataclass
class FigureBytes:
    """A single extracted figure, ready for filtering + description + disk write.

    ``width`` / ``height`` are the raster pixel dimensions of the embedded
    image resource. ``rect_width`` / ``rect_height`` are the rendered size
    on the page in PDF points — this is what determines visual area and
    is used by the position-independent filters.
    """

    page: int
    index: int
    xref: int
    mime_type: str  # "image/png", "image/jpeg", ...
    extension: str  # "png", "jpg", ...
    bytes: bytes
    width: int
    height: int
    # Rendered rect on the page (in PDF points, not pixels) — populated by
    # :func:`extract` via ``page.get_image_rects``. Non-rendered figures
    # (inline masks, etc.) get ``None`` and are skipped by the filters.
    rect_width: float | None = None
    rect_height: float | None = None
    page_width: float | None = None
    page_height: float | None = None
    content_hash: str = ""
    #: Fraction of pixels that belong to the most-common color (0–1).
    #: Populated at extract time via ``pymupdf.Pixmap.color_topusage``. A
    #: value close to 1 indicates a solid-color / near-empty rectangle,
    #: which the filter pipeline treats as decorative.
    dominant_color_usage: float | None = None

    @property
    def rel_area(self) -> float:
        """Fraction of the page covered by the rendered rect (0–1), or 0 if unknown."""
        if (
            self.rect_width is None
            or self.rect_height is None
            or not self.page_width
            or not self.page_height
        ):
            return 0.0
        return (self.rect_width * self.rect_height) / (self.page_width * self.page_height)

    @property
    def aspect(self) -> float:
        """Rendered aspect ratio w/h, or 0 if height is unknown."""
        if not self.rect_height:
            return 0.0
        return (self.rect_width or 0.0) / self.rect_height


@dataclass
class PageExtraction:
    """Per-page extracted text plus any figures on that page."""

    page_number: int  # 1-based
    text: str
    figures: list[FigureBytes] = field(default_factory=list)


@dataclass
class PdfExtraction:
    """Full PDF extraction result.

    ``pages`` preserves ordering; join with ``\\n\\n---\\n\\n`` for a flat text.
    ``figures`` is a flat convenience list across all pages.
    """

    pages: list[PageExtraction]

    @property
    def figures(self) -> list[FigureBytes]:
        return [fig for page in self.pages for fig in page.figures]

    def plain_text(self) -> str:
        return "\n\n".join(p.text.strip() for p in self.pages if p.text.strip())


def extract_text(path: Path) -> str:
    """Return just the concatenated text of every page in ``path``.

    Kept for backwards compatibility with callers that only need text.
    For full text+figures use :func:`extract`.
    """
    return extract(path).plain_text()


def _dominant_color_usage(image_bytes: bytes) -> float | None:
    """Return the fraction of pixels that belong to the most-common color.

    Used by the solid-color filter rule — a value near 1 means the image
    is essentially a uniform block (all-black, all-white, etc.) and is
    almost certainly decorative. Returns ``None`` if the image cannot be
    decoded.
    """
    try:
        pixmap = pymupdf.Pixmap(image_bytes)
        # PyMuPDF's color_topusage returns (usage, color).
        usage, _ = pixmap.color_topusage()
        return float(usage)
    except Exception as exc:  # noqa: BLE001 — PyMuPDF raises generic
        logger.debug("dominant-color computation failed: %s", exc)
        return None


def extract(path: Path, *, extract_figures: bool = True) -> PdfExtraction:
    """Return a :class:`PdfExtraction` with per-page text and figures.

    When ``extract_figures=True``, each figure is stamped with:
    - raster pixel dimensions (``width``/``height``)
    - rendered rect on the page (``rect_width``/``rect_height``)
    - page dimensions (``page_width``/``page_height``) for relative-area math
    - SHA256 content hash of the image bytes (for cross-page dedup filters)
    """
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise IngestError(f"encrypted PDF not supported: {path}")
        pages: list[PageExtraction] = []
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            text = str(page.get_text("text"))
            page_rect = page.rect
            figures: list[FigureBytes] = []
            if extract_figures:
                for fig_index, img_info in enumerate(page.get_images(full=True)):
                    xref = img_info[0]
                    try:
                        extracted = doc.extract_image(xref)
                    except Exception as exc:  # noqa: BLE001 — PyMuPDF raises generic
                        logger.debug("skip image xref=%d on page %d: %s", xref, page_index + 1, exc)
                        continue
                    img_bytes = extracted["image"]
                    rects = page.get_image_rects(xref)
                    rect = rects[0] if rects else None
                    dominant_usage = _dominant_color_usage(img_bytes)
                    figures.append(
                        FigureBytes(
                            page=page_index + 1,
                            index=fig_index + 1,
                            xref=xref,
                            mime_type=f"image/{extracted['ext']}",
                            extension=extracted["ext"],
                            bytes=img_bytes,
                            width=int(extracted.get("width", 0)),
                            height=int(extracted.get("height", 0)),
                            rect_width=float(rect.width) if rect else None,
                            rect_height=float(rect.height) if rect else None,
                            page_width=float(page_rect.width) if page_rect else None,
                            page_height=float(page_rect.height) if page_rect else None,
                            content_hash=hashlib.sha256(img_bytes).hexdigest(),
                            dominant_color_usage=dominant_usage,
                        )
                    )
            pages.append(PageExtraction(page_number=page_index + 1, text=text, figures=figures))
    return PdfExtraction(pages=pages)
