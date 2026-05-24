"""Embedding wrapper — fully mocked, no real API calls."""

from __future__ import annotations

from typing import Any

import pytest

from app.config.config import Settings
from app.core.llm import embeddings
from app.core.errors import ConfigError, KebabError


class _FakeEmbeddingItem:
    def __init__(self, values: list[float]) -> None:
        self.values = values


class _FakeResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.embeddings = [_FakeEmbeddingItem(v) for v in vectors]


class _FakeModels:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors
        self.calls: list[tuple[str, list[str]]] = []

    def embed_content(
        self, *, model: str, contents: list[str], config: object | None = None
    ) -> _FakeResponse:
        self.calls.append((model, list(contents)))
        return _FakeResponse(self._vectors[: len(contents)])


class _FakeClient:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.models = _FakeModels(vectors)


@pytest.fixture
def settings() -> Settings:
    return Settings(GOOGLE_API_KEY="test-key", EMBEDDING_MODEL="text-embedding-004")


@pytest.fixture(autouse=True)
def _reset_client_cache() -> None:
    embeddings._client.cache_clear()


def _patch_client(monkeypatch: pytest.MonkeyPatch, vectors: list[list[float]]) -> _FakeClient:
    client = _FakeClient(vectors)

    def _factory(api_key: str) -> Any:
        if not api_key:
            raise ConfigError("empty key")
        return client

    monkeypatch.setattr(embeddings, "_client", _factory)
    return client


def test_embed_returns_single_vector(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    client = _patch_client(monkeypatch, [[0.1, 0.2, 0.3]])
    result = embeddings.embed("hello", settings)
    assert result == [0.1, 0.2, 0.3]
    assert client.models.calls == [("text-embedding-004", ["hello"])]


def test_embed_batch_preserves_order(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    _patch_client(monkeypatch, [[1.0], [2.0], [3.0]])
    result = embeddings.embed_batch(["a", "b", "c"], settings)
    assert result == [[1.0], [2.0], [3.0]]


def test_embed_batch_empty_input_returns_empty(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    _patch_client(monkeypatch, [[1.0]])
    assert embeddings.embed_batch([], settings) == []


def test_embed_strips_provider_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(GOOGLE_API_KEY="test-key", EMBEDDING_MODEL="google-gla:text-embedding-004")
    client = _patch_client(monkeypatch, [[0.1]])
    embeddings.embed("hello", settings)
    assert client.models.calls[0][0] == "text-embedding-004"


def test_embed_raises_on_empty_api_key() -> None:
    with pytest.raises(ConfigError):
        embeddings._client("")


def test_embed_raises_on_api_failure(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    class _BoomModels:
        def embed_content(
            self, *, model: str, contents: list[str], config: object | None = None
        ) -> Any:
            raise RuntimeError("network down")

    class _BoomClient:
        models = _BoomModels()

    monkeypatch.setattr(embeddings, "_client", lambda _key: _BoomClient())
    with pytest.raises(KebabError, match="Gemini embedding call failed"):
        embeddings.embed("hi", settings)


def test_embed_raises_on_count_mismatch(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    # Fake returns only 1 vector for 2 inputs.
    _patch_client(monkeypatch, [[0.1]])
    with pytest.raises(KebabError, match="returned 1 embeddings"):
        embeddings.embed_batch(["a", "b"], settings)


def test_embed_batch_chunks_large_inputs(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Inputs > _MAX_BATCH_SIZE split into multiple provider calls, order preserved."""
    # 250 inputs at _MAX_BATCH_SIZE=100 → 3 chunks of 100, 100, 50
    n = 250
    inputs = [f"text-{i}" for i in range(n)]
    expected_vectors = [[float(i)] for i in range(n)]

    class _ChunkingModels:
        """Fake that returns exactly the chunk it was sent, in order."""

        def __init__(self) -> None:
            self.calls: list[list[str]] = []
            self._cursor = 0

        def embed_content(
            self, *, model: str, contents: list[str], config: object | None = None
        ) -> _FakeResponse:
            self.calls.append(list(contents))
            chunk = expected_vectors[self._cursor : self._cursor + len(contents)]
            self._cursor += len(contents)
            return _FakeResponse(chunk)

    class _ChunkingClient:
        def __init__(self) -> None:
            self.models = _ChunkingModels()

    client = _ChunkingClient()
    monkeypatch.setattr(embeddings, "_client", lambda _k: client)

    result = embeddings.embed_batch(inputs, settings)
    assert result == expected_vectors
    assert [len(c) for c in client.models.calls] == [100, 100, 50]


def test_embed_batch_exactly_at_chunk_boundary_uses_one_call(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Exactly _MAX_BATCH_SIZE inputs → single provider call (no empty trailing chunk)."""
    n = embeddings._MAX_BATCH_SIZE
    inputs = [f"text-{i}" for i in range(n)]
    expected_vectors = [[float(i)] for i in range(n)]
    client = _patch_client(monkeypatch, expected_vectors)

    result = embeddings.embed_batch(inputs, settings)
    assert result == expected_vectors
    assert len(client.models.calls) == 1
