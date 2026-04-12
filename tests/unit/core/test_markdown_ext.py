"""Tests for app.core.markdown_ext — KEBAB footnote plugin for marko."""

from __future__ import annotations

from marko import Markdown
from marko.md_renderer import MarkdownRenderer

from app.core.markdown_ext import FootnoteDef, FootnoteRef, make_extension


def _md() -> Markdown:
    return Markdown(renderer=MarkdownRenderer, extensions=["gfm", make_extension()])


class TestFootnoteDefParsing:
    def test_parses_basic_footnote(self) -> None:
        md = _md()
        tree = md.parse("[^1]: [Plate tectonics](https://en.wikipedia.org/wiki/Plate_tectonics)\n")
        defs = [n for n in tree.children if isinstance(n, FootnoteDef)]
        assert len(defs) == 1
        assert defs[0].number == 1
        assert defs[0].title == "Plate tectonics"
        assert defs[0].url == "https://en.wikipedia.org/wiki/Plate_tectonics"
        assert defs[0].source_id is None

    def test_parses_footnote_with_source_id(self) -> None:
        md = _md()
        tree = md.parse("[^1]: [42] [SCI10 Q1 M2](../../raw/documents/file.pdf)\n")
        defs = [n for n in tree.children if isinstance(n, FootnoteDef)]
        assert len(defs) == 1
        assert defs[0].number == 1
        assert defs[0].source_id == 42
        assert defs[0].title == "SCI10 Q1 M2"
        assert defs[0].url == "../../raw/documents/file.pdf"

    def test_parses_multiple_footnotes(self) -> None:
        md = _md()
        body = (
            "[^1]: [1] [Source A](raw/a.pdf)\n"
            "[^2]: [Wikipedia](https://en.wikipedia.org/wiki/X)\n"
            "[^3]: [2] [Source B](raw/b.pdf)\n"
        )
        tree = md.parse(body)
        defs = [n for n in tree.children if isinstance(n, FootnoteDef)]
        assert len(defs) == 3
        assert defs[0].number == 1
        assert defs[0].source_id == 1
        assert defs[1].number == 2
        assert defs[1].source_id is None
        assert defs[2].number == 3
        assert defs[2].source_id == 2

    def test_parses_url_with_spaces_encoded(self) -> None:
        md = _md()
        tree = md.parse("[^1]: [Title](../../raw/documents/SCI10%20Q1.pdf)\n")
        defs = [n for n in tree.children if isinstance(n, FootnoteDef)]
        assert len(defs) == 1
        assert "%20" in defs[0].url

    def test_fallback_for_raw_content(self) -> None:
        md = _md()
        tree = md.parse("[^1]: Just some raw text\n")
        defs = [n for n in tree.children if isinstance(n, FootnoteDef)]
        assert len(defs) == 1
        assert defs[0].number == 1
        assert defs[0].title == "Just some raw text"
        assert defs[0].url == ""
        assert defs[0].source_id is None


class TestFootnoteRefParsing:
    def test_parses_inline_ref(self) -> None:
        md = _md()
        tree = md.parse("Some text[^1] and more[^2].\n")
        # Refs are inside paragraph children
        para = tree.children[0]
        refs = [c for c in getattr(para, "children", []) if isinstance(c, FootnoteRef)]
        assert len(refs) == 2
        assert refs[0].number == 1
        assert refs[1].number == 2


class TestRoundtrip:
    def test_basic_footnote_roundtrips(self) -> None:
        md = _md()
        original = "[^1]: [Plate tectonics](https://en.wikipedia.org/wiki/Plate_tectonics)\n"
        tree = md.parse(original)
        rendered = md.render(tree)
        assert "[^1]: [Plate tectonics](https://en.wikipedia.org/wiki/Plate_tectonics)" in rendered

    def test_footnote_with_source_id_roundtrips(self) -> None:
        md = _md()
        original = "[^1]: [42] [Source Title](../../raw/documents/file.pdf)\n"
        tree = md.parse(original)
        rendered = md.render(tree)
        assert "[^1]: [42] [Source Title](../../raw/documents/file.pdf)" in rendered

    def test_inline_ref_roundtrips(self) -> None:
        md = _md()
        original = "Plates move due to convection[^1].\n"
        tree = md.parse(original)
        rendered = md.render(tree)
        assert "[^1]" in rendered
        assert "convection" in rendered

    def test_mixed_body_roundtrips(self) -> None:
        md = _md()
        original = (
            "# Topic\n\n"
            "Body text with a citation[^1] and another[^2].\n\n"
            "## Section Two\n\n"
            "More content.\n\n"
            "[^1]: [1] [Source A](raw/a.pdf)\n"
            "[^2]: [Wikipedia](https://en.wikipedia.org/wiki/X)\n"
        )
        tree = md.parse(original)
        rendered = md.render(tree)
        assert "# Topic" in rendered
        assert "## Section Two" in rendered
        assert "[^1]" in rendered
        assert "[^2]" in rendered
        assert "[Source A](raw/a.pdf)" in rendered
        assert "[Wikipedia](https://en.wikipedia.org/wiki/X)" in rendered

    def test_gfm_table_roundtrips(self) -> None:
        md = _md()
        original = (
            "| A | B |\n"
            "|---|---|\n"
            "| 1 | 2 |\n"
        )
        tree = md.parse(original)
        rendered = md.render(tree)
        assert "| A | B |" in rendered
        assert "| 1 | 2 |" in rendered
