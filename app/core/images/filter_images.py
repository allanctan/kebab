"""Deterministic filters that decide which extracted figures deserve an LLM describe call.

Every filter is pure code — no Gemini calls. Their purpose is to skip
decorative images (page headers, watermarks, bullet markers, separator
bars) before they hit the multimodal describer. On real DepEd-style
PDFs this cuts the describer cost by ~56%.

Ruleset (all thresholds tunable via :class:`Settings`):
    1. R1 Tiny        — figures covering less than ``FIGURE_MIN_REL_AREA``
                        of the page area are dropped (bullet markers, icons,
                        small inline decorations).
    2. R2 SolidColor  — figures where the single most-common color covers
                        ``FIGURE_SOLID_COLOR_THRESHOLD`` or more of the
                        pixels are dropped (blank rectangles, all-black
                        placeholders, uniform color blocks).
    3. R3 Repeated    — figures whose SHA256 content hash appears on
                        ``FIGURE_REPEAT_PAGE_THRESHOLD`` or more pages of the
                        same document are dropped (page headers, watermarks,
                        section dividers — regardless of size).
    4. R4 Ribbon      — thin figures with aspect ratio at least
                        ``FIGURE_RIBBON_ASPECT`` and smaller than
                        ``FIGURE_RIBBON_MAX_REL_AREA`` of the page are dropped
                        (separator bars, thin banners).

Why no position-based rule: empirical analysis of a real 13-PDF corpus
showed that the footer band (y > 0.85) was only 19% decorative and the
header band (y < 0.08) was only 51% decorative. Position alone is too
noisy to filter on without losing real content.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from app.config.config import Settings
from app.utils.pdf_extractor import FigureBytes


@dataclass(frozen=True)
class FilterDecision:
    """Outcome of running the filter stack on a single figure."""

    keep: bool
    # "" when keep is True; otherwise one of "tiny", "solid_color",
    # "repeated", "ribbon".
    reason: str


def build_hash_page_counts(figures: Iterable[FigureBytes]) -> dict[str, int]:
    """Count the number of distinct pages each content hash appears on.

    The filter asks "is this hash on ≥N pages?" so we count *distinct
    pages per hash*, not total occurrences. Two figures with the same
    hash on the same page count as one page.
    """
    hash_pages: dict[str, set[int]] = defaultdict(set)
    for fig in figures:
        if fig.content_hash:
            hash_pages[fig.content_hash].add(fig.page)
    return {h: len(pages) for h, pages in hash_pages.items()}


def decide(
    figure: FigureBytes,
    hash_page_counts: dict[str, int],
    settings: Settings,
) -> FilterDecision:
    """Return keep/drop decision for one figure. Order matters — cheapest first."""
    # R1 Tiny — constant-time check on precomputed rel_area.
    if figure.rel_area and figure.rel_area < settings.FIGURE_MIN_REL_AREA:
        return FilterDecision(keep=False, reason="tiny")

    # R2 Solid color — cheap field read populated at extract time.
    if (
        figure.dominant_color_usage is not None
        and figure.dominant_color_usage >= settings.FIGURE_SOLID_COLOR_THRESHOLD
    ):
        return FilterDecision(keep=False, reason="solid_color")

    # R3 Repeated bytes — dict lookup.
    if figure.content_hash:
        pages = hash_page_counts.get(figure.content_hash, 0)
        if pages >= settings.FIGURE_REPEAT_PAGE_THRESHOLD:
            return FilterDecision(keep=False, reason="repeated")

    # R4 Ribbon — shape check on rendered rect.
    if (
        figure.aspect >= settings.FIGURE_RIBBON_ASPECT
        and figure.rel_area
        and figure.rel_area < settings.FIGURE_RIBBON_MAX_REL_AREA
    ):
        return FilterDecision(keep=False, reason="ribbon")

    return FilterDecision(keep=True, reason="")




