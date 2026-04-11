"""YAML-backed model alias registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.llm import presets as model_presets
from app.core.errors import ConfigError


@pytest.fixture(autouse=True)
def _reset_registry_cache() -> None:
    model_presets.reload_registry()


def test_list_aliases_includes_starter_set() -> None:
    aliases = model_presets.list_aliases()
    assert "gemini-flash" in aliases
    assert "gemini-flash-lite" in aliases
    assert "gemini-pro" in aliases


def test_get_entry_returns_model_entry() -> None:
    entry = model_presets.get_entry("gemini-flash")
    assert entry is not None
    assert entry.provider == "google-gla"
    assert entry.model == "gemini-2.5-flash"
    assert entry.is_custom_endpoint is False
    assert entry.model_string == "google-gla:gemini-2.5-flash"


def test_get_entry_unknown_returns_none() -> None:
    assert model_presets.get_entry("nonexistent-alias") is None


def test_resolve_alias_native_returns_model_string() -> None:
    assert model_presets.resolve_alias("gemini-flash") == "google-gla:gemini-2.5-flash"
    assert model_presets.resolve_alias("gemini-pro") == "google-gla:gemini-2.5-pro"


def test_resolve_alias_unknown_raises_with_known_list() -> None:
    with pytest.raises(ConfigError, match="gemini-flash"):
        model_presets.resolve_alias("does-not-exist")


def test_load_registry_rejects_duplicate_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "models.yaml"
    bad.write_text(
        "models:\n"
        "  - alias: dup\n    provider: google-gla\n    model: a\n"
        "  - alias: dup\n    provider: google-gla\n    model: b\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(model_presets, "_REGISTRY_PATH", bad)
    model_presets.reload_registry()
    with pytest.raises(ConfigError, match="duplicate alias"):
        model_presets.list_aliases()


def test_load_registry_rejects_missing_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "models.yaml"
    bad.write_text(
        "models:\n  - alias: incomplete\n    provider: google-gla\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(model_presets, "_REGISTRY_PATH", bad)
    model_presets.reload_registry()
    with pytest.raises(ConfigError, match="missing required field"):
        model_presets.list_aliases()


def test_load_registry_missing_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_presets, "_REGISTRY_PATH", tmp_path / "nope.yaml")
    model_presets.reload_registry()
    with pytest.raises(ConfigError, match="not found"):
        model_presets.list_aliases()
