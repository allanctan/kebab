"""Tests for short_model_label."""

from __future__ import annotations

from app.core.llm.labels import short_model_label


class TestShortModelLabel:
    def test_anthropic_native_prefix(self) -> None:
        assert short_model_label("anthropic:claude-opus-4-7") == "claude-opus-4.7"

    def test_anthropic_bedrock_id(self) -> None:
        assert (
            short_model_label("bedrock:us.anthropic.claude-sonnet-4-6")
            == "claude-sonnet-4.6"
        )

    def test_google_gemini(self) -> None:
        assert short_model_label("google-gla:gemini-2.5-pro") == "gemini-2.5-pro"

    def test_google_gemini_preview(self) -> None:
        assert (
            short_model_label("google-gla:gemini-3.1-pro-preview") == "gemini-3.1-pro"
        )

    def test_openai(self) -> None:
        assert short_model_label("openai:gpt-5.4-mini") == "gpt-5.4-mini"

    def test_passthrough_for_unknown_provider(self) -> None:
        assert short_model_label("custom:my-model") == "my-model"

    def test_bare_alias_returned_as_is(self) -> None:
        # When the caller forgot to resolve the alias first, just return it
        assert short_model_label("opus-4.7") == "opus-4.7"
