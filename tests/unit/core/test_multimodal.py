"""Multimodal image describer — fully mocked."""

from __future__ import annotations

from typing import Any

import pytest

from app.config.config import Settings
from app.core.llm import multimodal
from app.core.errors import ConfigError, KebabError


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, response_text: str) -> None:
        self._text = response_text
        self.calls: list[tuple[str, Any, str]] = []

    def generate_content(self, *, model: str, contents: Any) -> _FakeResponse:
        self.calls.append((model, contents, self._text))
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, response_text: str) -> None:
        self.models = _FakeModels(response_text)


@pytest.fixture
def settings() -> Settings:
    return Settings(GOOGLE_API_KEY="test-key", FIGURE_MODEL="google-gla:gemini-2.5-flash-lite")


def _patch_client(monkeypatch: pytest.MonkeyPatch, response_text: str) -> _FakeClient:
    client = _FakeClient(response_text)
    monkeypatch.setattr(multimodal, "_client", lambda _key: client)
    return client


def test_describe_image_returns_caption(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    client = _patch_client(monkeypatch, "A red square diagram.")
    caption = multimodal.describe_image(
        b"fake-bytes",
        "image/png",
        settings,
        width=200,
        height=200,
    )
    assert caption == "A red square diagram."
    # Model was called once with the stripped prefix.
    assert client.models.calls[0][0] == "gemini-2.5-flash-lite"


def test_describe_image_returns_decorative_for_tiny_images(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    _patch_client(monkeypatch, "should not be called")
    caption = multimodal.describe_image(
        b"x",
        "image/png",
        settings,
        width=10,
        height=10,
    )
    assert caption == "DECORATIVE"


def test_describe_image_raises_on_empty_key() -> None:
    with pytest.raises(ConfigError):
        multimodal._client("")


def test_describe_image_wraps_api_errors(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    class _Boom:
        def generate_content(self, *, model: str, contents: Any) -> Any:
            raise RuntimeError("400 INVALID_ARGUMENT")  # permanent, no retry

    monkeypatch.setattr(multimodal, "_client", lambda _key: type("C", (), {"models": _Boom()})())
    monkeypatch.setattr(multimodal, "_BACKOFF_BASE", 0.0)
    with pytest.raises(KebabError, match="multimodal call failed"):
        multimodal.describe_image(b"x", "image/png", settings, width=200, height=200)


def test_describe_image_retries_transient_failures(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """503 Service Unavailable should retry and eventually succeed."""

    class _FlakyModels:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, *, model: str, contents: Any) -> Any:
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("status_code: 503 UNAVAILABLE")
            return _FakeResponse("A real caption.")

    flaky = _FlakyModels()
    monkeypatch.setattr(
        multimodal, "_client", lambda _key: type("C", (), {"models": flaky})()
    )
    monkeypatch.setattr(multimodal, "_BACKOFF_BASE", 0.0)  # no sleep in tests
    result = multimodal.describe_image(
        b"x", "image/png", settings, width=200, height=200
    )
    assert result == "A real caption."
    assert flaky.calls == 3  # succeeded on the 3rd attempt


def test_describe_image_gives_up_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    class _AlwaysDown:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, *, model: str, contents: Any) -> Any:
            self.calls += 1
            raise RuntimeError("status_code: 503 UNAVAILABLE")

    always = _AlwaysDown()
    monkeypatch.setattr(
        multimodal, "_client", lambda _key: type("C", (), {"models": always})()
    )
    monkeypatch.setattr(multimodal, "_BACKOFF_BASE", 0.0)
    with pytest.raises(KebabError, match="multimodal call failed"):
        multimodal.describe_image(b"x", "image/png", settings, width=200, height=200)
    assert always.calls == multimodal._MAX_ATTEMPTS  # tried every attempt


def test_describe_image_does_not_retry_permanent_errors(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """A 400-class error should raise on the first attempt without retrying."""

    class _PermanentError:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, *, model: str, contents: Any) -> Any:
            self.calls += 1
            raise RuntimeError("400 INVALID_ARGUMENT")

    perm = _PermanentError()
    monkeypatch.setattr(
        multimodal, "_client", lambda _key: type("C", (), {"models": perm})()
    )
    monkeypatch.setattr(multimodal, "_BACKOFF_BASE", 0.0)
    with pytest.raises(KebabError):
        multimodal.describe_image(b"x", "image/png", settings, width=200, height=200)
    assert perm.calls == 1  # one attempt, no retries


def test_describe_image_treats_empty_response_as_decorative(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    _patch_client(monkeypatch, "")
    assert (
        multimodal.describe_image(b"x", "image/png", settings, width=200, height=200)
        == "DECORATIVE"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "DECORATIVE",
        "DECORATIVE.",
        "decorative",
        "Decorative.",
        "DECORATIVE!",
        "  DECORATIVE  ",
        "DECORATIVE - no pedagogical content",
        "The figure displays a black screen. DECORATIVE.",  # suffix form
        "Page header\nDECORATIVE",  # last-line form
    ],
)
def test_describe_image_normalizes_decorative_variants(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, raw: str
) -> None:
    _patch_client(monkeypatch, raw)
    assert (
        multimodal.describe_image(b"x", "image/png", settings, width=200, height=200)
        == "DECORATIVE"
    )


def test_describe_image_preserves_real_descriptions(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    _patch_client(monkeypatch, "A decorative wreath around a photo.")
    # "decorative" as a word inside a real description must NOT trigger the sentinel.
    result = multimodal.describe_image(b"x", "image/png", settings, width=200, height=200)
    assert result != "DECORATIVE"
    assert "wreath" in result
