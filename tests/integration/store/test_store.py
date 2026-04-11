"""Qdrant store wrapper against an in-memory client."""

from __future__ import annotations

import pytest
from qdrant_client import QdrantClient

from app.config.config import Settings
from app.core.store import EMBEDDING_DIM, Store
from app.models.article import Article


def _vec(seed: float = 0.0) -> list[float]:
    return [seed] * EMBEDDING_DIM


def _article(id: str, *, domain: str = "Science", parent: str | None = None) -> Article:
    return Article(
        id=id,
        name=id,
        description=f"desc for {id}",
        keywords=[],
        faq=[],
        level_type="article",
        parent_ids=[parent] if parent else [],
        depth=2,
        position=0,
        domain=domain,
        subdomain="Biology",
        prerequisites=[],
        related=[],
        md_path=f"{id}.md",
        confidence_level=2,
    )


@pytest.fixture
def store() -> Store:
    settings = Settings(QDRANT_PATH=None, QDRANT_URL=None)
    return Store(settings, client=QdrantClient(":memory:"))


@pytest.mark.integration
def test_ensure_collection_is_idempotent(store: Store) -> None:
    store.ensure_collection()
    store.ensure_collection()  # second call must not raise


@pytest.mark.integration
def test_upsert_and_search(store: Store) -> None:
    store.ensure_collection()
    a = _article("SCI-001")
    b = _article("SCI-002")
    store.upsert([(a, _vec(0.1)), (b, _vec(0.9))])

    hits = store.search(_vec(0.1), limit=2)
    assert {hit.article.id for hit in hits} == {"SCI-001", "SCI-002"}
    assert hits[0].article.id == "SCI-001"  # closer to query vector


@pytest.mark.integration
def test_retrieve_by_id(store: Store) -> None:
    store.ensure_collection()
    a = _article("SCI-001")
    store.upsert([(a, _vec(0.1))])
    retrieved = store.retrieve(["SCI-001"])
    assert len(retrieved) == 1
    assert retrieved[0].name == "SCI-001"


@pytest.mark.integration
def test_scroll_yields_all_payloads(store: Store) -> None:
    store.ensure_collection()
    store.upsert(
        [(_article(f"SCI-{i:03d}"), _vec(float(i) / 100)) for i in range(5)]
    )
    seen = sorted(article.id for article in store.scroll())
    assert seen == [f"SCI-{i:03d}" for i in range(5)]


@pytest.mark.integration
def test_delete_by_filter_for_idempotent_resync(store: Store) -> None:
    store.ensure_collection()
    a = _article("SCI-001", domain="Science")
    b = _article("HIST-001", domain="History")
    store.upsert([(a, _vec(0.1)), (b, _vec(0.2))])
    assert store.count() == 2

    store.delete_by_filter(Store.domain_filter("Science"))
    remaining = list(store.scroll())
    assert [art.id for art in remaining] == ["HIST-001"]


@pytest.mark.integration
def test_count_with_filter(store: Store) -> None:
    store.ensure_collection()
    store.upsert(
        [
            (_article("A", domain="Science"), _vec(0.1)),
            (_article("B", domain="Science"), _vec(0.2)),
            (_article("C", domain="History"), _vec(0.3)),
        ]
    )
    assert store.count() == 3
    assert store.count(Store.domain_filter("Science")) == 2
