"""Tests for app.agents.research_images.targets."""

from __future__ import annotations

from app.agents.research_images.targets import WikiTarget, extract_wikipedia_targets


class TestExtractWikipediaTargets:
    def test_extracts_basic_footnote(self) -> None:
        body = (
            "Body text with [^1] a citation.\n\n"
            "[^1]: [Plate tectonics](https://en.wikipedia.org/wiki/Plate_tectonics)\n"
        )
        targets = extract_wikipedia_targets(body)
        assert len(targets) == 1
        assert targets[0] == WikiTarget(
            title="Plate tectonics",
            url="https://en.wikipedia.org/wiki/Plate_tectonics",
        )

    def test_decodes_url_encoded_titles(self) -> None:
        body = (
            "[^1]: [Convergent boundary](https://en.wikipedia.org/wiki/Convergent%20boundary)\n"
        )
        targets = extract_wikipedia_targets(body)
        assert len(targets) == 1
        assert targets[0].title == "Convergent boundary"

    def test_dedupes_by_url(self) -> None:
        body = (
            "[^1]: [Plates](https://en.wikipedia.org/wiki/Plate_tectonics)\n"
            "[^2]: [Same article](https://en.wikipedia.org/wiki/Plate_tectonics)\n"
            "[^3]: [Other](https://en.wikipedia.org/wiki/Subduction)\n"
        )
        targets = extract_wikipedia_targets(body)
        assert len(targets) == 2
        assert targets[0].url.endswith("Plate_tectonics")
        assert targets[1].url.endswith("Subduction")

    def test_skips_non_wikipedia_footnotes(self) -> None:
        body = (
            "[^1]: [PDF source](https://example.com/file.pdf)\n"
            "[^2]: [Wiki](https://en.wikipedia.org/wiki/X)\n"
        )
        targets = extract_wikipedia_targets(body)
        assert len(targets) == 1
        assert targets[0].title == "X"

    def test_tolerates_inline_source_id_prefix(self) -> None:
        # Today's writer sometimes prepends a [N] source-id before the link.
        body = "[^1]: [42] [Plate tectonics](https://en.wikipedia.org/wiki/Plate_tectonics)\n"
        targets = extract_wikipedia_targets(body)
        assert len(targets) == 1
        assert targets[0].title == "Plate tectonics"

    def test_no_footnotes_returns_empty(self) -> None:
        assert extract_wikipedia_targets("# Plain markdown\n\nNo footnotes here.\n") == []
