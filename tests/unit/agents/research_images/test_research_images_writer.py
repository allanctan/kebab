"""Tests for app.agents.research_images.writer."""

from __future__ import annotations

from pathlib import Path

from app.agents.research_images.fetcher import ImageCandidate
from app.agents.research_images.writer import append_figure_refs


def _candidate(filename: str, llm_desc: str = "", raw_desc: str = "") -> ImageCandidate:
    return ImageCandidate(
        local_path=Path("figures") / "slug" / filename,
        source_title="Wikipedia: X",
        raw_description=raw_desc,
        llm_description=llm_desc,
    )


class TestAppendFigureRefs:
    def test_appends_markdown_image_refs(self) -> None:
        body = "# Article\n\nSome content.\n"
        candidates = [
            _candidate("wiki-diagram.png", llm_desc="Side-view diagram of a fault."),
            _candidate("wiki-map.svg", llm_desc="World map of plate boundaries."),
        ]
        result = append_figure_refs(body, candidates, article_slug="article-slug")
        assert "![Side-view diagram of a fault.](figures/article-slug/wiki-diagram.png)" in result
        assert "![World map of plate boundaries.](figures/article-slug/wiki-map.svg)" in result

    def test_empty_candidates_returns_body_unchanged(self) -> None:
        body = "# Article\n\nContent.\n"
        assert append_figure_refs(body, [], article_slug="x") == body

    def test_falls_back_to_raw_description(self) -> None:
        body = "# Article\n\n"
        c = _candidate("img.png", llm_desc="", raw_desc="raw caption from API")
        result = append_figure_refs(body, [c], article_slug="x")
        assert "![raw caption from API](figures/x/img.png)" in result

    def test_falls_back_to_filename_when_no_descriptions(self) -> None:
        body = "# Article\n\n"
        c = _candidate("img.png", llm_desc="", raw_desc="")
        result = append_figure_refs(body, [c], article_slug="x")
        assert "![img.png](figures/x/img.png)" in result

    def test_truncates_long_descriptions(self) -> None:
        body = "# Article\n\n"
        long_desc = "x" * 300
        c = _candidate("img.png", llm_desc=long_desc)
        result = append_figure_refs(body, [c], article_slug="x")
        # The description in the alt text is capped at 150 chars
        assert ("x" * 150) in result
        assert ("x" * 151) not in result.split("![", 1)[-1].split("]", 1)[0]
