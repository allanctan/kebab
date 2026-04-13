from __future__ import annotations

import textwrap
from pathlib import Path

from app.core.verticals import load_verticals, resolve_vertical


class TestLoadVerticals:
    def test_loads_yaml_files(self, tmp_path: Path, mock_env) -> None:
        kebab_dir = tmp_path / ".kebab"
        kebab_dir.mkdir()
        (kebab_dir / "education.yaml").write_text(
            textwrap.dedent("""\
                description: K-12 education content
                generate_instruction: Write for students
                authoritative_sources:
                  - deped.gov.ph
                  - britannica.com
                classification_fields:
                  grade:
                    type: int
                    range: [1, 12]
            """)
        )
        mock_env.KNOWLEDGE_DIR = str(tmp_path)
        result = load_verticals(mock_env)
        assert "education" in result
        v = result["education"]
        assert v.key == "education"
        assert v.description == "K-12 education content"
        assert v.authoritative_sources == ["deped.gov.ph", "britannica.com"]

    def test_returns_empty_when_no_kebab_dir(self, tmp_path: Path, mock_env) -> None:
        mock_env.KNOWLEDGE_DIR = str(tmp_path)
        result = load_verticals(mock_env)
        assert result == {}

    def test_skips_non_yaml_files(self, tmp_path: Path, mock_env) -> None:
        kebab_dir = tmp_path / ".kebab"
        kebab_dir.mkdir()
        (kebab_dir / "sources.json").write_text("{}")
        (kebab_dir / "education.yaml").write_text("description: test\n")
        mock_env.KNOWLEDGE_DIR = str(tmp_path)
        result = load_verticals(mock_env)
        assert list(result.keys()) == ["education"]


class TestResolveVertical:
    def test_resolves_from_contexts_key(self, tmp_path: Path, mock_env) -> None:
        kebab_dir = tmp_path / ".kebab"
        kebab_dir.mkdir()
        (kebab_dir / "education.yaml").write_text(
            "description: K-12\nauthoritative_sources:\n  - deped.gov.ph\n"
        )
        mock_env.KNOWLEDGE_DIR = str(tmp_path)

        class FakeFm:
            def model_dump(self) -> dict:
                return {"contexts": {"education": {"grade": 10}}}

        result = resolve_vertical(mock_env, FakeFm())  # type: ignore[arg-type]
        assert result is not None
        assert result.key == "education"

    def test_returns_none_when_no_contexts(self, tmp_path: Path, mock_env) -> None:
        mock_env.KNOWLEDGE_DIR = str(tmp_path)

        class FakeFm:
            def model_dump(self) -> dict:
                return {}

        result = resolve_vertical(mock_env, FakeFm())  # type: ignore[arg-type]
        assert result is None
