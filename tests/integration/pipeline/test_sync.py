"""Sync stage end-to-end against in-memory Qdrant + mocked embeddings."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from app.config.config import Settings
from app.core.store import EMBEDDING_DIM, Store
from app.pipeline import sync as sync_stage

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "articles"


def _stub_embed(texts: list[str], _settings: Settings) -> list[list[float]]:
    # Deterministic 768-dim vectors so re-runs give the same result.
    return [[(i % 7) * 0.1 + 0.01 * j for j in range(EMBEDDING_DIM)] for i in range(len(texts))]


@pytest.fixture
def populated_knowledge(knowledge_dir: Path) -> Path:
    """Drop two fixture articles into ``knowledge/curated/Science/Biology/``."""
    science = knowledge_dir / "curated" / "Science" / "Biology"
    science.mkdir(parents=True)
    shutil.copy(FIXTURES / "photosynthesis.md", science / "photosynthesis.md")
    shutil.copy(FIXTURES / "cellular_respiration.md", science / "cellular_respiration.md")
    return knowledge_dir


@pytest.fixture
def settings(populated_knowledge: Path) -> Settings:
    return Settings(
        KNOWLEDGE_DIR=populated_knowledge,
        CURATED_DIR=populated_knowledge / "curated",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )


@pytest.fixture
def store(settings: Settings) -> Store:
    return Store(settings, client=QdrantClient(":memory:"))


@pytest.mark.integration
def test_sync_indexes_articles(settings: Settings, store: Store) -> None:
    result = sync_stage.run(settings, store=store, embed_fn=_stub_embed)
    assert result.articles == 2
    assert result.skipped == []
    assert sum(result.confidence_histogram.values()) == 2

    indexed = sorted(article.id for article in store.scroll())
    assert indexed == ["SCI-BIO-001", "SCI-BIO-002"]


@pytest.mark.integration
def test_sync_extracts_faq_into_payload(settings: Settings, store: Store) -> None:
    sync_stage.run(settings, store=store, embed_fn=_stub_embed)
    photo = next(a for a in store.scroll() if a.id == "SCI-BIO-001")
    assert photo.faq == [
        "What is photosynthesis?",
        "Where does photosynthesis happen in a plant cell?",
    ]
    assert photo.confidence_level == 1  # 2 sources, no verifications yet → level 1


@pytest.mark.integration
def test_sync_is_idempotent(settings: Settings, store: Store) -> None:
    first = sync_stage.run(settings, store=store, embed_fn=_stub_embed)
    second = sync_stage.run(settings, store=store, embed_fn=_stub_embed)
    assert first.articles == second.articles == 2
    assert store.count() == 2  # not 4 — re-running does not duplicate.


@pytest.mark.integration
def test_sync_assigns_domain_subdomain_from_path(
    settings: Settings, store: Store
) -> None:
    sync_stage.run(settings, store=store, embed_fn=_stub_embed)
    for article in store.scroll():
        assert article.domain == "Science"
        assert article.subdomain == "Biology"


@pytest.mark.integration
def test_sync_skips_oversized_articles(
    populated_knowledge: Path, settings: Settings, store: Store
) -> None:
    huge = populated_knowledge / "curated" / "Science" / "Biology" / "huge.md"
    huge.write_text(
        "---\nid: SCI-BIO-HUGE\nname: Huge\ntype: article\nsources: []\n---\n\n"
        + ("word " * 80_000),
        encoding="utf-8",
    )
    result = sync_stage.run(settings, store=store, embed_fn=_stub_embed)
    assert result.articles == 2
    assert any("token" in reason for _, reason in result.skipped)


@pytest.mark.integration
def test_sync_no_articles_returns_empty_summary(tmp_path: Path) -> None:
    empty_root = tmp_path / "empty_knowledge"
    empty_root.mkdir()
    settings = Settings(
        KNOWLEDGE_DIR=empty_root,
        CURATED_DIR=empty_root / "curated",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )
    fresh_store = Store(settings, client=QdrantClient(":memory:"))
    result = sync_stage.run(settings, store=fresh_store, embed_fn=_stub_embed)
    assert result.articles == 0
    assert result.confidence_histogram == {}
