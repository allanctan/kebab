"""Organize stage with a stubbed hierarchy proposer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config.config import Settings
from app.pipeline.organize.agent import HierarchyNode, HierarchyPlan
from app.pipeline import organize as organize_stage


def _write_sources_json(knowledge: Path) -> None:
    """Write a minimal sources.json so manifest labels carry [N] IDs."""
    kebab_dir = knowledge / ".kebab"
    kebab_dir.mkdir(parents=True, exist_ok=True)
    (kebab_dir / "sources.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "id": 1,
                        "stem": "openstax_chapter_8",
                        "raw_path": "raw/documents/openstax_chapter_8.pdf",
                        "title": "Openstax Chapter 8",
                        "tier": 1,
                        "checksum": "abc",
                        "adapter": "pdf",
                        "retrieved_at": None,
                    },
                    {
                        "id": 2,
                        "stem": "deped_grade7",
                        "raw_path": "raw/documents/deped_grade7.pdf",
                        "title": "DepEd Grade 7",
                        "tier": 2,
                        "checksum": "def",
                        "adapter": "pdf",
                        "retrieved_at": None,
                    },
                ],
                "next_id": 3,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    # Processed tree holds the extracted text; organize reads per-source folders.
    processed = knowledge / "processed" / "documents"
    (processed / "openstax_chapter_8").mkdir(parents=True)
    (processed / "deped_grade7").mkdir(parents=True)
    (processed / "openstax_chapter_8" / "text.md").write_text(
        "Photosynthesis converts light into chemical energy stored in glucose.",
        encoding="utf-8",
    )
    (processed / "deped_grade7" / "text.md").write_text(
        "Plants use chloroplasts to capture sunlight and produce sugars.",
        encoding="utf-8",
    )
    _write_sources_json(knowledge)
    return Settings(
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        PROCESSED_DIR=knowledge / "processed",
        CURATED_DIR=knowledge / "curated",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )


def _canned_plan() -> HierarchyPlan:
    return HierarchyPlan(
        nodes=[
            HierarchyNode(
                id="SCI",
                name="Science",
                level_type="domain",
                parent_id=None,
                description="Natural sciences for K-12.",
            ),
            HierarchyNode(
                id="SCI-BIO",
                name="Biology",
                level_type="subdomain",
                parent_id="SCI",
                description="Living organisms.",
            ),
            HierarchyNode(
                id="SCI-BIO-PHO",
                name="Photosynthesis",
                level_type="topic",
                parent_id="SCI-BIO",
                description="Light into glucose.",
            ),
            HierarchyNode(
                id="SCI-BIO-001",
                name="Light Reactions",
                level_type="article",
                parent_id="SCI-BIO-PHO",
                description="The light-dependent stage of photosynthesis.",
                source_files=[1],
            ),
            HierarchyNode(
                id="SCI-BIO-002",
                name="Calvin Cycle",
                level_type="article",
                parent_id="SCI-BIO-PHO",
                description="The light-independent stage of photosynthesis.",
                source_files=[2],
            ),
        ]
    )


def _stub_proposer(
    _settings: Settings, _domain: str, _manifest: list[tuple[str, str]]
) -> HierarchyPlan:
    return _canned_plan()


@pytest.mark.integration
def test_organize_creates_article_stubs(settings: Settings) -> None:
    result = organize_stage.run(settings, domain_hint="Science", proposer=_stub_proposer)
    assert len(result.created) == 2
    assert all(path.exists() for path in result.created)
    light = settings.CURATED_DIR / "Science" / "Biology" / "light-reactions.md"
    assert light.exists()
    body = light.read_text(encoding="utf-8")
    assert "SCI-BIO-001" in body
    assert "TODO" in body


@pytest.mark.integration
def test_organize_skips_non_article_nodes(settings: Settings) -> None:
    result = organize_stage.run(settings, domain_hint="Science", proposer=_stub_proposer)
    # Only the 2 article-level nodes should produce files.
    assert len(result.created) == 2


@pytest.mark.integration
def test_organize_does_not_overwrite_existing(settings: Settings) -> None:
    organize_stage.run(settings, domain_hint="Science", proposer=_stub_proposer)
    light = settings.CURATED_DIR / "Science" / "Biology" / "light-reactions.md"
    light.write_text("---\nid: X\nname: Custom\ntype: article\nsources: []\n---\nedited\n", encoding="utf-8")
    result = organize_stage.run(settings, domain_hint="Science", proposer=_stub_proposer)
    assert light in result.existing
    assert "edited" in light.read_text()


@pytest.mark.integration
def test_organize_persists_plan_with_paths(settings: Settings) -> None:
    result = organize_stage.run(settings, domain_hint="Science", proposer=_stub_proposer)
    assert result.plan_path.exists()
    reloaded = organize_stage.load_plan(settings, "Science")
    assert reloaded is not None
    article_nodes = [n for n in reloaded.nodes if n.level_type == "article"]
    assert all(n.md_path is not None for n in article_nodes)
    light_node = next(n for n in article_nodes if n.id == "SCI-BIO-001")
    assert light_node.md_path is not None
    assert light_node.md_path.endswith("Science/Biology/light-reactions.md")


@pytest.mark.integration
def test_organize_is_idempotent_via_cache(settings: Settings) -> None:
    """Second run loads the cached plan instead of re-calling the proposer."""
    calls: list[int] = []

    def _counting_proposer(*args: object, **kwargs: object) -> HierarchyPlan:
        calls.append(1)
        return _canned_plan()

    first = organize_stage.run(settings, proposer=_counting_proposer)
    assert first.loaded_from_cache is False
    assert len(calls) == 1

    second = organize_stage.run(settings, proposer=_counting_proposer)
    assert second.loaded_from_cache is True
    assert len(calls) == 1  # proposer not called again
    assert {n.id for n in second.plan.nodes} == {n.id for n in first.plan.nodes}


@pytest.mark.integration
def test_organize_force_bypasses_cache(settings: Settings) -> None:
    calls: list[int] = []

    def _counting_proposer(*args: object, **kwargs: object) -> HierarchyPlan:
        calls.append(1)
        return _canned_plan()

    organize_stage.run(settings, proposer=_counting_proposer)
    organize_stage.run(settings, proposer=_counting_proposer, force=True)
    assert len(calls) == 2


@pytest.mark.integration
def test_organize_restores_missing_stubs_from_cache(settings: Settings) -> None:
    first = organize_stage.run(settings, proposer=_stub_proposer)
    # Operator deletes an article stub out-of-band.
    first.created[0].unlink()
    second = organize_stage.run(settings, proposer=_stub_proposer)
    assert second.loaded_from_cache is True
    assert first.created[0] in second.created
    assert first.created[0].exists()


@pytest.mark.integration
def test_organize_runs_incremental_when_new_source_appears(settings: Settings) -> None:
    """Add a PDF after the initial plan and confirm incremental proposer runs."""
    # Seed the initial plan.
    organize_stage.run(settings, domain_hint="Science", proposer=_stub_proposer)

    # Operator ingests a new PDF — a third folder under processed/documents.
    new_dir = settings.PROCESSED_DIR / "documents" / "new_third_source"
    new_dir.mkdir(parents=True)
    (new_dir / "text.md").write_text(
        "Cellular respiration converts glucose into ATP.", encoding="utf-8"
    )

    # Register new_third_source in sources.json so it gets an ID.
    sources_path = settings.KNOWLEDGE_DIR / ".kebab" / "sources.json"
    sources_data = json.loads(sources_path.read_text(encoding="utf-8"))
    sources_data["sources"].append(
        {
            "id": 3,
            "stem": "new_third_source",
            "raw_path": "raw/documents/new_third_source.pdf",
            "title": "New Third Source",
            "tier": 1,
            "checksum": "ghi",
            "adapter": "pdf",
            "retrieved_at": None,
        }
    )
    sources_data["next_id"] = 4
    sources_path.write_text(json.dumps(sources_data), encoding="utf-8")

    captured: dict[str, object] = {}

    def _incremental(
        _settings: Settings,
        existing: HierarchyPlan,
        new_manifest: list[tuple[str, str]],
    ) -> HierarchyPlan:
        captured["existing_ids"] = {n.id for n in existing.nodes}
        captured["new_names"] = [name for name, _ in new_manifest]
        # Attach the new source to an existing article AND create one new article.
        return HierarchyPlan(
            nodes=[
                HierarchyNode(
                    id="SCI-BIO-001",  # extends existing
                    name="Light Reactions",
                    level_type="article",
                    parent_id="SCI-BIO-PHO",
                    description="The light-dependent stage of photosynthesis.",
                    source_files=[3],
                ),
                HierarchyNode(
                    id="SCI-BIO-003",  # net-new article
                    name="Cellular Respiration",
                    level_type="article",
                    parent_id="SCI-BIO",
                    description="Glucose → ATP.",
                    source_files=[3],
                ),
            ]
        )

    result = organize_stage.run(
        settings,
        domain_hint="Science",
        proposer=_stub_proposer,
        incremental_proposer=_incremental,
    )

    assert result.loaded_from_cache is True
    assert result.extended_articles == ["SCI-BIO-001"]
    assert result.added_articles == ["SCI-BIO-003"]
    assert captured["new_names"] == ["[3] New Third Source"]
    assert "SCI-BIO-001" in captured["existing_ids"]  # type: ignore[operator]

    # Merged plan persists both extension and addition.
    reloaded = organize_stage.load_plan(settings, "Science")
    assert reloaded is not None
    light = next(n for n in reloaded.nodes if n.id == "SCI-BIO-001")
    assert 3 in light.source_files
    assert 1 in light.source_files
    respiration = next(n for n in reloaded.nodes if n.id == "SCI-BIO-003")
    assert respiration.md_path is not None
    assert respiration.md_path.endswith(
        "Science/Biology/cellular-respiration.md"
    )
    # Stub for the new article is on disk.
    assert Path(respiration.md_path).exists()


@pytest.mark.integration
def test_organize_skips_incremental_when_no_new_sources(settings: Settings) -> None:
    """Cache hit with zero new sources should not touch the incremental proposer."""
    organize_stage.run(settings, proposer=_stub_proposer)

    incremental_calls: list[int] = []

    def _should_not_run(
        _s: Settings, _p: HierarchyPlan, _m: list[tuple[str, str]]
    ) -> HierarchyPlan:
        incremental_calls.append(1)
        return HierarchyPlan(nodes=[])

    result = organize_stage.run(
        settings,
        proposer=_stub_proposer,
        incremental_proposer=_should_not_run,
    )
    assert result.loaded_from_cache is True
    assert result.extended_articles == []
    assert result.added_articles == []
    assert incremental_calls == []


@pytest.mark.integration
def test_organize_empty_manifest_returns_empty_plan(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    (knowledge / "processed" / "documents").mkdir(parents=True)
    settings = Settings(
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        PROCESSED_DIR=knowledge / "processed",
        CURATED_DIR=knowledge / "curated",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )
    result = organize_stage.run(settings, proposer=_stub_proposer)
    assert result.created == []
    assert result.plan.nodes == []
    # Empty plan is still persisted so re-runs short-circuit.
    assert result.plan_path.exists()
