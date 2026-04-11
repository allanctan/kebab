"""Embedding generation via Google Gemini.

KEBAB calls ``google-genai`` directly for embeddings rather than going
through pydantic-ai — pydantic-ai's embedding API is geared toward agent
runs, and a single sync call is simpler. The model defaults to
``text-embedding-004`` (768 dimensions); switching dimensions means
bumping :data:`app.core.store.EMBEDDING_DIM` and re-syncing.

Anti-pattern *not* copied from better-ed-ai: silent fallback when the
embedding API errors. KEBAB raises :class:`KebabError` so sync fails
loudly.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Protocol

from app.config.config import Settings
from app.core.errors import ConfigError, KebabError

logger = logging.getLogger(__name__)


class _EmbedClient(Protocol):
    def embed_content(self, *, model: str, contents: list[str]) -> Any: ...


@lru_cache(maxsize=1)
def _client(api_key: str) -> Any:
    """Lazily build and cache the genai client. Empty key → ConfigError."""
    if not api_key:
        raise ConfigError("KEBAB_GOOGLE_API_KEY is empty — required for embeddings")
    import google.genai as genai  # noqa: PLC0415 — namespace package, lazy import

    return genai.Client(api_key=api_key)


def _resolve_model(settings: Settings) -> str:
    """Strip any provider prefix from the configured embedding model."""
    raw = settings.EMBEDDING_MODEL
    return raw.split(":", 1)[1] if ":" in raw else raw


def embed(text: str, settings: Settings) -> list[float]:
    """Return a single embedding vector for ``text``."""
    return embed_batch([text], settings)[0]


def embed_batch(texts: list[str], settings: Settings) -> list[list[float]]:
    """Return embeddings for a batch of strings, preserving order.

    Empty input list → empty output. The genai SDK handles batching, so
    we forward the whole list in one call. ``output_dimensionality`` is
    forwarded from :data:`Settings.EMBEDDING_DIM` — ``gemini-embedding-001``
    supports Matryoshka reduction down to any dim <= 3072.
    """
    if not texts:
        return []
    from google.genai import types  # noqa: PLC0415 — lazy import, namespace pkg

    client = _client(settings.GOOGLE_API_KEY)
    model = _resolve_model(settings)
    try:
        response = client.models.embed_content(
            model=model,
            contents=texts,
            config=types.EmbedContentConfig(
                output_dimensionality=settings.EMBEDDING_DIM
            ),
        )
    except Exception as exc:  # noqa: BLE001 — translate to KebabError
        raise KebabError(f"Gemini embedding call failed: {exc}") from exc
    embeddings = getattr(response, "embeddings", None)
    if not embeddings:
        raise KebabError(f"Gemini embedding response missing 'embeddings': {response}")
    vectors: list[list[float]] = []
    for item in embeddings:
        values = getattr(item, "values", None)
        if values is None:
            raise KebabError(f"Gemini embedding item missing 'values': {item}")
        vectors.append(list(values))
    if len(vectors) != len(texts):
        raise KebabError(
            f"Gemini returned {len(vectors)} embeddings for {len(texts)} inputs"
        )
    return vectors
