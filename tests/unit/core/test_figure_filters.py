"""Figure filter rules — pure code, no LLM calls."""

from __future__ import annotations

from app.config.config import Settings
from app.core.images.filters import (
    build_hash_page_counts,
    decide,
    partition,
)
from app.utils.pdf_extractor import FigureBytes

# A US Letter page, 612 × 792 pt = 484,704 pt². 2% ≈ 9,694 pt² (~98×98 pt).
PAGE_W = 612.0
PAGE_H = 792.0


def _fig(
    *,
    page: int = 1,
    index: int = 1,
    rect_w: float = 200.0,
    rect_h: float = 150.0,
    hash_: str = "unique-hash",
    dominant_color_usage: float | None = 0.5,
) -> FigureBytes:
    return FigureBytes(
        page=page,
        index=index,
        xref=1,
        mime_type="image/png",
        extension="png",
        bytes=b"\x89PNG",
        width=400,
        height=300,
        rect_width=rect_w,
        rect_height=rect_h,
        page_width=PAGE_W,
        page_height=PAGE_H,
        content_hash=hash_,
        dominant_color_usage=dominant_color_usage,
    )


def _settings() -> Settings:
    return Settings(GOOGLE_API_KEY="test-key")


# ----- R1: tiny ---------------------------------------------------------------


def test_r1_tiny_drops_figures_below_threshold() -> None:
    # 30x30 pt ≈ 0.19% of the page → below default 0.5% threshold.
    fig = _fig(rect_w=30, rect_h=30)
    result = decide(fig, {fig.content_hash: 1}, _settings())
    assert result.keep is False
    assert result.reason == "tiny"


def test_r1_tiny_keeps_figures_above_threshold() -> None:
    # 200x150 pt ≈ 6.19% of the page → above default 2%.
    fig = _fig(rect_w=200, rect_h=150)
    result = decide(fig, {fig.content_hash: 1}, _settings())
    assert result.keep is True
    assert result.reason == ""


def test_r1_tiny_threshold_respects_settings_override() -> None:
    settings = Settings(GOOGLE_API_KEY="test-key", FIGURE_MIN_REL_AREA=0.10)
    # 200x150 pt ≈ 6.19% → below the overridden 10% threshold.
    fig = _fig(rect_w=200, rect_h=150)
    result = decide(fig, {fig.content_hash: 1}, settings)
    assert result.keep is False
    assert result.reason == "tiny"


# ----- R2: cross-page repetition ----------------------------------------------


def test_r2_repeated_drops_when_hash_hits_threshold() -> None:
    fig = _fig(rect_w=200, rect_h=150, hash_="seal-bytes")
    counts = {"seal-bytes": 3}
    result = decide(fig, counts, _settings())
    assert result.keep is False
    assert result.reason == "repeated"


def test_r2_repeated_keeps_when_hash_below_threshold() -> None:
    fig = _fig(rect_w=200, rect_h=150, hash_="seal-bytes")
    counts = {"seal-bytes": 2}
    result = decide(fig, counts, _settings())
    assert result.keep is True


def test_r2_build_hash_page_counts_counts_distinct_pages() -> None:
    # Same hash on pages 1, 2, 3 → count is 3, not 4 (even though there are 4 figures).
    figures = [
        _fig(page=1, index=1, hash_="X"),
        _fig(page=1, index=2, hash_="X"),  # same page, same hash — still 1 page
        _fig(page=2, index=1, hash_="X"),
        _fig(page=3, index=1, hash_="X"),
        _fig(page=1, index=3, hash_="Y"),  # different hash
    ]
    counts = build_hash_page_counts(figures)
    assert counts["X"] == 3
    assert counts["Y"] == 1


# ----- R2: solid color --------------------------------------------------------


def test_r2_solid_color_drops_uniform_rectangles() -> None:
    # 99.5% of pixels are one color → above the 0.99 default threshold → drop.
    fig = _fig(rect_w=200, rect_h=150, dominant_color_usage=0.995)
    result = decide(fig, {fig.content_hash: 1}, _settings())
    assert result.keep is False
    assert result.reason == "solid_color"


def test_r2_solid_color_keeps_varied_rectangles() -> None:
    # 50% dominant usage = plenty of variation → keep.
    fig = _fig(rect_w=200, rect_h=150, dominant_color_usage=0.5)
    result = decide(fig, {fig.content_hash: 1}, _settings())
    assert result.keep is True


def test_r2_solid_color_none_usage_is_ignored() -> None:
    # Extractor couldn't compute the stat (e.g. decode failure) → pass through.
    fig = _fig(rect_w=200, rect_h=150, dominant_color_usage=None)
    result = decide(fig, {fig.content_hash: 1}, _settings())
    assert result.keep is True


def test_r2_solid_color_threshold_respects_settings_override() -> None:
    settings = Settings(GOOGLE_API_KEY="test-key", FIGURE_SOLID_COLOR_THRESHOLD=0.60)
    fig = _fig(rect_w=200, rect_h=150, dominant_color_usage=0.70)
    result = decide(fig, {fig.content_hash: 1}, settings)
    assert result.keep is False
    assert result.reason == "solid_color"


# ----- R4: ribbon -------------------------------------------------------------


def test_r4_ribbon_drops_thin_wide_small() -> None:
    # 500x35 pt: rel_area ≈ 3.6%; aspect ≈ 14.3 → matches ribbon.
    fig = _fig(rect_w=500, rect_h=35)
    result = decide(fig, {fig.content_hash: 1}, _settings())
    assert result.keep is False
    assert result.reason == "ribbon"


def test_r4_ribbon_keeps_wide_large_figures() -> None:
    # 500x100 pt: rel_area ≈ 10.3%, above the 5% ribbon-max threshold → keep.
    fig = _fig(rect_w=500, rect_h=100)
    result = decide(fig, {fig.content_hash: 1}, _settings())
    assert result.keep is True


def test_r4_ribbon_keeps_square_figures_even_if_small() -> None:
    # 150x150 pt aspect 1, well above tiny floor → keep.
    fig = _fig(rect_w=150, rect_h=150)
    result = decide(fig, {fig.content_hash: 1}, _settings())
    assert result.keep is True


# ----- interaction: rule priority ---------------------------------------------


def test_tiny_takes_precedence_over_solid_color() -> None:
    fig = _fig(rect_w=20, rect_h=20, dominant_color_usage=0.995)
    result = decide(fig, {fig.content_hash: 1}, _settings())
    assert result.reason == "tiny"


def test_tiny_takes_precedence_over_repeated() -> None:
    fig = _fig(rect_w=20, rect_h=20, hash_="X")
    result = decide(fig, {"X": 100}, _settings())
    assert result.reason == "tiny"


def test_solid_color_takes_precedence_over_repeated() -> None:
    fig = _fig(rect_w=200, rect_h=150, hash_="X", dominant_color_usage=0.99)
    result = decide(fig, {"X": 100}, _settings())
    assert result.reason == "solid_color"


def test_repeated_takes_precedence_over_ribbon() -> None:
    fig = _fig(rect_w=500, rect_h=35, hash_="X")  # ribbon-shaped
    result = decide(fig, {"X": 5}, _settings())
    assert result.reason == "repeated"


# ----- partition helper -------------------------------------------------------


def test_partition_splits_into_kept_and_dropped() -> None:
    figures = [
        _fig(page=1, index=1, rect_w=200, rect_h=150, hash_="content-A"),  # keep
        _fig(page=1, index=2, rect_w=20, rect_h=20, hash_="tiny-icon"),  # tiny
        _fig(
            page=1, index=3, rect_w=200, rect_h=150, hash_="blank-page",
            dominant_color_usage=0.99,
        ),  # solid_color
        _fig(page=2, index=1, rect_w=200, rect_h=150, hash_="seal"),  # repeated
        _fig(page=3, index=1, rect_w=200, rect_h=150, hash_="seal"),  # repeated
        _fig(page=4, index=1, rect_w=200, rect_h=150, hash_="seal"),  # repeated
        _fig(page=2, index=2, rect_w=500, rect_h=35, hash_="ribbon-A"),  # ribbon
    ]
    kept, dropped = partition(figures, _settings())
    assert len(kept) == 1
    assert kept[0][0].content_hash == "content-A"
    assert {d[1].reason for d in dropped} == {"tiny", "solid_color", "repeated", "ribbon"}


def test_partition_keeps_everything_when_no_filters_match() -> None:
    figures = [
        _fig(page=p, index=1, rect_w=200, rect_h=150, hash_=f"h{p}")
        for p in range(1, 6)
    ]
    kept, dropped = partition(figures, _settings())
    assert len(kept) == 5
    assert dropped == []


def test_figures_without_rect_skip_size_filters() -> None:
    """Figures PyMuPDF couldn't locate on the page (no rects) are passed through."""
    fig = FigureBytes(
        page=1,
        index=1,
        xref=1,
        mime_type="image/png",
        extension="png",
        bytes=b"x",
        width=400,
        height=300,
        rect_width=None,
        rect_height=None,
        page_width=None,
        page_height=None,
        content_hash="h",
    )
    result = decide(fig, {"h": 1}, _settings())
    assert result.keep is True  # no info → can't filter by shape; let describer decide
