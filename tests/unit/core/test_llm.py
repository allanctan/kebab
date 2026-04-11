"""Env expansion + model resolution dispatch."""

from __future__ import annotations

import pytest

from app.config import env
from app.core.llm import resolve as llm
from app.core.errors import ConfigError


def test_expand_env_passthrough_when_no_dollar() -> None:
    assert llm._expand_env("google-gla:gemini-2.5-flash") == "google-gla:gemini-2.5-flash"


def test_expand_env_returns_none_for_none() -> None:
    assert llm._expand_env(None) is None


def test_expand_env_dollar_var_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env, "GEMINI_MODEL", "google-gla:gemini-2.5-pro")
    assert llm._expand_env("$GEMINI_MODEL") == "google-gla:gemini-2.5-pro"


def test_expand_env_braced_var_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env, "FAST_MODEL", "google-gla:gemini-2.5-flash-lite")
    assert llm._expand_env("${FAST_MODEL}") == "google-gla:gemini-2.5-flash-lite"


def test_expand_env_missing_setting_raises() -> None:
    with pytest.raises(ConfigError, match="NONEXISTENT_SETTING"):
        llm._expand_env("$NONEXISTENT_SETTING")


def test_expand_env_empty_setting_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env, "GOOGLE_API_KEY", "")
    with pytest.raises(ConfigError):
        llm._expand_env("$GOOGLE_API_KEY")


def test_resolve_model_native_prefix_passthrough() -> None:
    assert llm.resolve_model("google-gla:gemini-2.5-flash") == "google-gla:gemini-2.5-flash"
    assert llm.resolve_model("openai:gpt-4o") == "openai:gpt-4o"
    assert llm.resolve_model("anthropic:claude-sonnet") == "anthropic:claude-sonnet"


def test_resolve_model_unknown_prefix_passthrough_with_warning() -> None:
    # Unknown prefix is not registered → returns verbatim (let pydantic-ai err later).
    assert llm.resolve_model("xyz:foo") == "xyz:foo"


def test_resolve_model_dollar_ref_expands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env, "GEMINI_MODEL", "google-gla:gemini-2.5-flash")
    assert llm.resolve_model("$GEMINI_MODEL") == "google-gla:gemini-2.5-flash"


def test_resolve_model_alias_dispatches_to_presets() -> None:
    # `gemini-flash` is defined in app/config/models.yaml.
    assert llm.resolve_model("gemini-flash") == "google-gla:gemini-2.5-flash"


def test_register_decorator_adds_factory() -> None:
    captured: list[str] = []

    @llm._register("dummy")
    def _factory(model_string: str) -> str:
        captured.append(model_string)
        return f"DUMMY({model_string})"

    try:
        assert llm.resolve_model("dummy:foo") == "DUMMY(dummy:foo)"
        assert captured == ["dummy:foo"]
    finally:
        llm._FACTORIES.pop("dummy", None)
