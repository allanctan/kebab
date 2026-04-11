"""Qdrant wrapper.

Thin facade over ``qdrant-client`` that handles local-file mode (M3 only;
server mode raises :class:`NotImplementedError` until needed). Every
pipeline stage talks to Qdrant through this class — no direct
``QdrantClient`` imports elsewhere in the codebase.

Vector dimensionality is hard-coded to 768 to match Gemini's
``text-embedding-004``. Bumping models means bumping this constant and
re-syncing.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from app.config.config import Settings
from app.core.errors import KebabError
from app.models.article import Article

logger = logging.getLogger(__name__)

#: Default embedding dimensionality. Overridden per-instance from
#: ``Settings.EMBEDDING_DIM`` when available; the constant is kept for
#: test fixtures that build vectors without a Settings object.
EMBEDDING_DIM = 768

#: Stable namespace for deriving Qdrant point UUIDs from article IDs.
#: Qdrant requires point IDs to be UUIDs or unsigned ints; KEBAB article IDs
#: ("SCI-BIO-001") are neither, so we hash them through ``uuid5``. The
#: original ID is preserved in the payload's ``id`` field.
_POINT_ID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_DNS


def _point_id(article_id: str) -> str:
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, article_id))


class ScoredArticle(BaseModel):
    """Search hit: an :class:`Article` plus its similarity score."""

    model_config = ConfigDict(extra="forbid")

    article: Article = Field(..., description="The retrieved article payload.")
    score: float = Field(..., description="Cosine similarity (higher is closer).")


def _article_to_payload(article: Article) -> dict[str, Any]:
    return article.model_dump(mode="json")


def _payload_to_article(payload: dict[str, Any]) -> Article:
    return Article.model_validate(payload)


def _eq(field: str, value: Any) -> FieldCondition:
    return FieldCondition(key=field, match=MatchValue(value=value))


def _in(field: str, values: list[Any]) -> FieldCondition:
    return FieldCondition(key=field, match=MatchAny(any=values))


class Store:
    """KEBAB's Qdrant wrapper. All stages go through this class."""

    def __init__(self, settings: Settings, *, client: QdrantClient | None = None) -> None:
        self.settings = settings
        if client is not None:
            self._client = client
        elif settings.QDRANT_URL:
            raise NotImplementedError(
                "QDRANT_URL (server mode) is not implemented yet. "
                "Set QDRANT_PATH for local file mode."
            )
        elif settings.QDRANT_PATH:
            self._client = QdrantClient(path=settings.QDRANT_PATH)
        else:
            raise KebabError("Either QDRANT_URL or QDRANT_PATH must be set.")
        self._collection = settings.QDRANT_COLLECTION
        self._dim = getattr(settings, "EMBEDDING_DIM", EMBEDDING_DIM)

    # ---- collection management -----------------------------------------------

    def ensure_collection(self) -> None:
        """Create the collection + payload indexes. Idempotent."""
        if not self._client.collection_exists(self._collection):
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=self._dim, distance=Distance.COSINE),
            )
            logger.info("created Qdrant collection %s (dim=%d)", self._collection, self._dim)
        for field, schema in (
            ("id", PayloadSchemaType.KEYWORD),
            ("level_type", PayloadSchemaType.KEYWORD),
            ("domain", PayloadSchemaType.KEYWORD),
            ("subdomain", PayloadSchemaType.KEYWORD),
            ("parent_ids", PayloadSchemaType.KEYWORD),
            ("confidence_level", PayloadSchemaType.INTEGER),
        ):
            try:
                self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field,
                    field_schema=schema,
                )
            except Exception as exc:  # noqa: BLE001 — qdrant-client raises generic on dup
                logger.debug("payload index %s already present (%s)", field, exc)

    # ---- writes --------------------------------------------------------------

    def upsert(self, points: list[tuple[Article, list[float]]]) -> None:
        """Upsert ``(article, vector)`` pairs in one batch.

        Article ID is used as the Qdrant point ID — re-running sync overwrites.
        """
        if not points:
            return
        payloads = [
            PointStruct(
                id=_point_id(article.id),
                vector=vector,
                payload=_article_to_payload(article),
            )
            for article, vector in points
        ]
        self._client.upsert(collection_name=self._collection, points=payloads)

    def delete_by_filter(self, filters: Filter) -> None:
        """Delete every point matching ``filters``. Used by sync for idempotency."""
        self._client.delete(
            collection_name=self._collection,
            points_selector=filters,
        )

    # ---- reads ---------------------------------------------------------------

    def search(
        self,
        query_vector: list[float],
        *,
        filters: Filter | None = None,
        limit: int = 10,
    ) -> list[ScoredArticle]:
        """Vector search with optional payload filter."""
        result = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            query_filter=filters,
            limit=limit,
            with_payload=True,
        )
        hits: list[ScoredArticle] = []
        for point in result.points:
            if point.payload is None:
                continue
            hits.append(
                ScoredArticle(article=_payload_to_article(point.payload), score=point.score)
            )
        return hits

    def scroll(self, filters: Filter | None = None, *, batch: int = 256) -> Iterator[Article]:
        """Yield every payload matching ``filters``."""
        offset: Any = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection,
                scroll_filter=filters,
                limit=batch,
                offset=offset,
                with_payload=True,
            )
            for point in points:
                if point.payload is None:
                    continue
                yield _payload_to_article(point.payload)
            if offset is None:
                return

    def retrieve(self, ids: list[str]) -> list[Article]:
        """Fetch articles by their IDs (preserves order of the result, not input)."""
        if not ids:
            return []
        records = self._client.retrieve(
            collection_name=self._collection,
            ids=[_point_id(article_id) for article_id in ids],
            with_payload=True,
        )
        return [
            _payload_to_article(record.payload)
            for record in records
            if record.payload is not None
        ]

    def count(self, filters: Filter | None = None) -> int:
        """Return the number of points matching ``filters`` (or all)."""
        return self._client.count(
            collection_name=self._collection, count_filter=filters, exact=True
        ).count

    # ---- helpers -------------------------------------------------------------

    @staticmethod
    def domain_filter(domain: str) -> Filter:
        return Filter(must=[_eq("domain", domain)])

    @staticmethod
    def level_filter(level_type: str) -> Filter:
        return Filter(must=[_eq("level_type", level_type)])

    @staticmethod
    def parent_filter(parent_id: str) -> Filter:
        return Filter(must=[_eq("parent_ids", parent_id)])

    @staticmethod
    def ids_filter(ids: list[str]) -> Filter:
        return Filter(must=[_in("id", ids)])
