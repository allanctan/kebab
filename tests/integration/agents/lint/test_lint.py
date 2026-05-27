"""Lint agent against a crafted knowledge tree + in-memory Qdrant."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from app.agents.lint import agent as lint_agent
from app.config.config import Settings
from app.core.store import EMBEDDING_DIM, Store
from app.models.article import Article


def _vec() -> list[float]:
    return [0.0] * EMBEDDING_DIM


def _today() -> date:
    return date(2026, 4, 9)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    biology = knowledge / "curated" / "Science" / "Biology"
    biology.mkdir(parents=True)

    # Article with no sources (missing_sources).
    (biology / "no_sources.md").write_text(
        "---\nid: SCI-BIO-001\nname: x\ntype: article\nsources: []\n---\n\nbody",
        encoding="utf-8",
    )
    # Article with stale verification.
    (biology / "stale.md").write_text(
        "---\n"
        "id: SCI-BIO-002\nname: y\ntype: article\n"
        "sources:\n  - id: 0\n    title: t\n    tier: 2\n"
        "verifications:\n  - model: m\n    passed: true\n    date: 2024-01-01\n"
        "---\n\nbody",
        encoding="utf-8",
    )
    return Settings(
        KNOWLEDGE_DIR=knowledge,
        CURATED_DIR=knowledge / "curated",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )


@pytest.fixture
def store(settings: Settings) -> Store:
    s = Store(settings, client=QdrantClient(":memory:"))
    s.ensure_collection()
    # Two indexed articles: one orphan (no parents), one below the gate.
    s.upsert(
        [
            (
                Article(
                    id="SCI-BIO-001",
                    name="x",
                    description="d",
                    keywords=[],
                    parent_ids=[],  # orphan
                    depth=2,
                    domain="Science",
                    subdomain="Biology",
                    md_path=None,
                    confidence_level=0,  # below gate
                ),
                _vec(),
            ),
            (
                Article(
                    id="SCI-BIO-002",
                    name="y",
                    description="d",
                    keywords=[],
                    parent_ids=["SCI-BIO"],  # has parent
                    depth=2,
                    domain="Science",
                    subdomain="Biology",
                    md_path=None,
                    confidence_level=3,  # at gate
                ),
                _vec(),
            ),
        ]
    )
    return s


@pytest.mark.integration
def test_lint_finds_missing_sources(settings: Settings, store: Store) -> None:
    result = lint_agent.run(settings, store=store, today=_today)
    codes = {issue.code for issue in result.report.issues}
    assert "missing_sources" in codes


@pytest.mark.integration
def test_lint_finds_stale_verification(settings: Settings, store: Store) -> None:
    result = lint_agent.run(settings, store=store, today=_today)
    codes = {issue.code for issue in result.report.issues}
    assert "stale_verification" in codes


@pytest.mark.integration
def test_lint_finds_orphan_articles(settings: Settings, store: Store) -> None:
    result = lint_agent.run(settings, store=store, today=_today)
    orphans = [i for i in result.report.issues if i.code == "orphan"]
    assert any(i.article_id == "SCI-BIO-001" for i in orphans)


@pytest.mark.integration
def test_lint_flags_below_gate(settings: Settings, store: Store) -> None:
    result = lint_agent.run(settings, store=store, today=_today)
    below = [i for i in result.report.issues if i.code == "below_confidence_gate"]
    assert any(i.article_id == "SCI-BIO-001" for i in below)
    assert all(i.article_id != "SCI-BIO-002" for i in below)


@pytest.mark.integration
def test_lint_writes_json_report(settings: Settings, store: Store) -> None:
    result = lint_agent.run(settings, store=store, today=_today)
    assert result.output_path.exists()
    assert result.report.articles_scanned == 2
    assert sum(result.report.counts.values()) == len(result.report.issues)


@pytest.mark.integration
def test_lint_surfaces_uncovered_competencies(
    settings: Settings, store: Store, tmp_path: Path
) -> None:
    """When a coverage JSON exists, lint reports per-spine uncovered LCs."""
    import json

    curriculum_dir = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "curriculum"
    curriculum_dir.mkdir(parents=True)
    coverage = {
        "name": "test-spine",
        "generated_at": "2026-05-22",
        "total_competencies": 3,
        "covered": 1,
        "uncovered": 2,
        "by_subject": {},
        "competencies": {
            "LC-1": ["SCI-BIO-001"],  # covered
            "LC-2": [],  # uncovered
            "LC-3": [],  # uncovered
        },
    }
    (curriculum_dir / "test-spine.coverage.json").write_text(
        json.dumps(coverage), encoding="utf-8"
    )

    result = lint_agent.run(settings, store=store, today=_today)
    assert "test-spine" in result.report.uncovered_competencies
    assert result.report.uncovered_competencies["test-spine"] == ["LC-2", "LC-3"]


@pytest.mark.integration
def test_lint_no_uncovered_when_no_spine(settings: Settings, store: Store) -> None:
    """Without a coverage JSON, uncovered_competencies stays empty."""
    result = lint_agent.run(settings, store=store, today=_today)
    assert result.report.uncovered_competencies == {}
