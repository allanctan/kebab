"""Gaps stage reads the canonical plan written by organize."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from app.config.config import Settings
from app.core.errors import KebabError
from app.agents.organize.agent import HierarchyNode, HierarchyPlan
from app.core.store import EMBEDDING_DIM, Store
from app.models.article import Article
from app.agents.generate import gaps as gaps_stage
from app.agents import organize as organize_stage


def _vec() -> list[float]:
    return [0.0] * EMBEDDING_DIM


def _fixed_now() -> datetime:
    return datetime(2026, 4, 9, 12, 0, 0)


def _canned_plan() -> HierarchyPlan:
    return HierarchyPlan(
        nodes=[
            HierarchyNode(
                id="SCI", name="Science", level_type="domain", description="natural sciences"
            ),
            HierarchyNode(
                id="SCI-BIO",
                name="Biology",
                level_type="subdomain",
                parent_id="SCI",
                description="living organisms",
            ),
            HierarchyNode(
                id="SCI-BIO-001",
                name="Photosynthesis",
                level_type="article",
                parent_id="SCI-BIO",
                description="light into glucose",
                source_files=[1],
            ),
            HierarchyNode(
                id="SCI-BIO-002",
                name="Cellular Respiration",
                level_type="article",
                parent_id="SCI-BIO",
                description="glucose into ATP",
                source_files=[2],
            ),
        ]
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    processed = knowledge / "processed" / "documents"
    (processed / "openstax").mkdir(parents=True)
    (processed / "deped").mkdir(parents=True)
    (processed / "openstax" / "text.md").write_text("Photosynthesis text.", encoding="utf-8")
    (processed / "deped" / "text.md").write_text("Respiration text.", encoding="utf-8")
    return Settings(
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        PROCESSED_DIR=knowledge / "processed",
        CURATED_DIR=knowledge / "curated",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )


@pytest.fixture
def store(settings: Settings) -> Store:
    return Store(settings, client=QdrantClient(":memory:"))


def _seed_plan(settings: Settings) -> None:
    organize_stage.run(
        settings,
        proposer=lambda _s, _d, _m: _canned_plan(),
    )


@pytest.mark.integration
def test_gaps_finds_all_missing_when_index_empty(
    settings: Settings, store: Store
) -> None:
    _seed_plan(settings)
    result = gaps_stage.run(settings, domain="Knowledge", store=store, now=_fixed_now)
    assert {gap.id for gap in result.report.gaps} == {"SCI-BIO-001", "SCI-BIO-002"}
    assert result.report.existing == []


@pytest.mark.integration
def test_gap_carries_target_path_from_plan(settings: Settings, store: Store) -> None:
    _seed_plan(settings)
    result = gaps_stage.run(settings, domain="Knowledge", store=store, now=_fixed_now)
    photo = next(gap for gap in result.report.gaps if gap.id == "SCI-BIO-001")
    assert photo.target_path is not None
    assert photo.target_path.endswith("curated/Science/Biology/photosynthesis.md")


@pytest.mark.integration
def test_gaps_skips_already_indexed(settings: Settings, store: Store) -> None:
    _seed_plan(settings)
    store.ensure_collection()
    store.upsert(
        [
            (
                Article(
                    id="SCI-BIO-001",
                    name="Photosynthesis",
                    description="x",
                    keywords=[],
                    faq=[],
                    level_type="article",
                    parent_ids=[],
                    depth=2,
                    position=0,
                    domain="Science",
                    subdomain="Biology",
                    prerequisites=[],
                    related=[],
                    md_path=None,
                    confidence_level=1,
                ),
                _vec(),
            )
        ]
    )
    result = gaps_stage.run(settings, domain="Knowledge", store=store, now=_fixed_now)
    assert [gap.id for gap in result.report.gaps] == ["SCI-BIO-002"]
    assert result.report.existing == ["SCI-BIO-001"]


@pytest.mark.integration
def test_gaps_raises_without_plan(settings: Settings, store: Store) -> None:
    with pytest.raises(KebabError):
        gaps_stage.run(settings, domain="Knowledge", store=store)


@pytest.mark.integration
def test_gaps_flags_stale_when_plan_has_new_sources(
    settings: Settings, store: Store
) -> None:
    """Article in index + frontmatter missing a plan source → stale gap."""
    _seed_plan(settings)
    store.ensure_collection()
    store.upsert(
        [
            (
                Article(
                    id="SCI-BIO-001",
                    name="Photosynthesis",
                    description="x",
                    keywords=[],
                    faq=[],
                    level_type="article",
                    parent_ids=[],
                    depth=2,
                    position=0,
                    domain="Science",
                    subdomain="Biology",
                    prerequisites=[],
                    related=[],
                    md_path=None,
                    confidence_level=1,
                ),
                _vec(),
            )
        ]
    )

    # The seeded plan's SCI-BIO-001 already has source_files=[1].
    # Stamp the curated article with a matching source (id=1) — it should NOT be
    # stale yet.
    photo_path = settings.CURATED_DIR / "Science" / "Biology" / "photosynthesis.md"
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    photo_path.write_text(
        "---\n"
        "id: SCI-BIO-001\n"
        "name: Photosynthesis\n"
        "type: article\n"
        "sources:\n"
        "  - id: 1\n"
        "    title: OpenStax Biology\n"
        "    tier: 2\n"
        "---\n\ngenerated body\n",
        encoding="utf-8",
    )
    result = gaps_stage.run(settings, domain="Knowledge", store=store, now=_fixed_now)
    assert "SCI-BIO-001" in result.report.existing
    assert all(gap.id != "SCI-BIO-001" for gap in result.report.gaps)

    # Now extend the plan with a new source ID on the existing article.
    plan_path = organize_stage.plan_path(settings, "Knowledge")
    plan = organize_stage.load_plan(settings, "Knowledge")
    assert plan is not None
    for node in plan.nodes:
        if node.id == "SCI-BIO-001":
            node.source_files = [1, 3]
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    stale = gaps_stage.run(settings, domain="Knowledge", store=store, now=_fixed_now)
    stale_gap = next(gap for gap in stale.report.gaps if gap.id == "SCI-BIO-001")
    assert stale_gap.reason == "stale"
    assert stale_gap.source_files == [1, 3]
    assert "SCI-BIO-001" not in stale.report.existing


@pytest.mark.integration
def test_gaps_does_not_flag_stale_when_source_stems_missing(
    settings: Settings, store: Store
) -> None:
    """Pre-migration articles (no source_stems key) must NOT be flagged stale.

    Otherwise every article in an existing KB would be regenerated on the
    first post-upgrade run. Migration path: force regen manually via
    ``kebab organize --force``.
    """
    _seed_plan(settings)
    store.ensure_collection()
    store.upsert(
        [
            (
                Article(
                    id="SCI-BIO-001",
                    name="Photosynthesis",
                    description="x",
                    keywords=[],
                    faq=[],
                    level_type="article",
                    parent_ids=[],
                    depth=2,
                    position=0,
                    domain="Science",
                    subdomain="Biology",
                    prerequisites=[],
                    related=[],
                    md_path=None,
                    confidence_level=1,
                ),
                _vec(),
            )
        ]
    )
    # The organize stub has no sources key with IDs.
    result = gaps_stage.run(settings, domain="Knowledge", store=store, now=_fixed_now)
    assert "SCI-BIO-001" in result.report.existing
    assert all(gap.id != "SCI-BIO-001" for gap in result.report.gaps)
