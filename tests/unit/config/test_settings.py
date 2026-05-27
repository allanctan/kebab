"""Tests for Settings — focusing on per-operation model fields."""

from __future__ import annotations

from app.config.config import Settings


class TestPerOperationModels:
    def test_gap_categorizer_defaults_to_gemini_flash(self) -> None:
        s = Settings()
        assert s.GAP_CATEGORIZER_MODEL == "gemini-flash"

    def test_ai_answerer_defaults_to_opus(self) -> None:
        s = Settings()
        assert s.AI_ANSWERER_MODEL == "opus-4.7"

    def test_ai_verifier_defaults_to_gemini_pro(self) -> None:
        s = Settings()
        assert s.AI_VERIFIER_MODEL == "gemini-pro"

    def test_can_override_via_constructor(self) -> None:
        s = Settings(AI_ANSWERER_MODEL="sonnet-4.6", AI_VERIFIER_MODEL="gemini-flash")
        assert s.AI_ANSWERER_MODEL == "sonnet-4.6"
        assert s.AI_VERIFIER_MODEL == "gemini-flash"
