"""Integration test for the research agent orchestrator (claim verification)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.research import research as research_stage
from app.agents.research.batch_verifier import BatchFinding
from app.agents.research.planner import ClaimEntry, ResearchPlan, SearchQuery
from app.config.config import Settings
from app.core.markdown import read_article, write_article
from app.core.research import searcher as searcher_module
from app.core.sources.index import SourceEntry, SourceIndex, save_index
from app.models.frontmatter import FrontmatterSchema
from app.models.source import Source


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    curated = knowledge / "curated" / "Science"
    curated.mkdir(parents=True)
    (knowledge / "raw" / "documents").mkdir(parents=True)
    (knowledge / "processed" / "documents").mkdir(parents=True)
    (knowledge / ".kebab").mkdir(parents=True)

    index = SourceIndex(
        sources=[
            SourceEntry(
                id=1,
                stem="test",
                raw_path="raw/documents/test.pdf",
                title="Test Source",
                tier=1,
                checksum="abc",
                adapter="local_pdf",
            )
        ],
        next_id=2,
    )
    save_index(index, knowledge / ".kebab" / "sources.json")

    fm = FrontmatterSchema(
        id="SCI-001",
        name="Plate Tectonics",
        type="article",
        sources=[Source(id=1, title="Test Source", tier=1)],
    )
    body = (
        "# Plate Tectonics\n\n"
        "Plates move due to convection currents.\n\n"
        "[^1]: [1] [Test Source](../../raw/documents/test.pdf)\n"
    )
    write_article(curated / "plate-tectonics.md", fm, body)

    return Settings(
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        PROCESSED_DIR=knowledge / "processed",
        CURATED_DIR=knowledge / "curated",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )


# ---------------------------------------------------------------------------
# Stubs — injected via monkeypatch since the orchestrator has no callable
# swap-points on run().  We stub plan_research, search, and batch_verify.
# ---------------------------------------------------------------------------


def _stub_plan_research(_settings: Settings, _deps: object) -> ResearchPlan:
    return ResearchPlan(
        claims=[
            ClaimEntry(
                text="Plates move due to convection currents",
                section="Plate Tectonics",
                paragraph=1,
            )
        ],
        queries=[
            SearchQuery(
                query="plate tectonics convection",
                adapter="wikipedia",
                target_claims=[0],
            )
        ],
    )


def _stub_search(
    _settings: Settings, _adapter: str, _query: str, **_kw: object
) -> list[searcher_module.SourceContent]:
    return [
        searcher_module.SourceContent(
            title="Wikipedia: Plate tectonics",
            url="https://en.wikipedia.org/wiki/Plate_tectonics",
            content="Convection currents in the mantle drive plate movement.",
        )
    ]


def _stub_batch_verify_confirm(
    _settings: Settings, _deps: object
) -> list[BatchFinding]:
    return [
        BatchFinding(
            claim_idx=0,
            outcome="confirm",
            source_title="Wikipedia: Plate tectonics",
            source_url="https://en.wikipedia.org/wiki/Plate_tectonics",
            evidence_quote="Convection currents in the mantle drive plate movement.",
            reasoning="Source confirms convection drives plates.",
        )
    ]


def _stub_batch_verify_dispute(
    _settings: Settings, _deps: object
) -> list[BatchFinding]:
    return [
        BatchFinding(
            claim_idx=0,
            outcome="dispute",
            source_title="Wikipedia: Plate tectonics",
            source_url="https://en.wikipedia.org/wiki/Plate_tectonics",
            evidence_quote="Slab pull is dominant.",
            contradiction="Source says slab pull, not convection, is primary.",
            dispute_category="factual_error",
            reasoning="Source contradicts claim.",
        )
    ]


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    batch_verify_stub: object,
) -> None:
    monkeypatch.setattr(research_stage, "plan_research", _stub_plan_research)
    monkeypatch.setattr(research_stage, "search", _stub_search)
    monkeypatch.setattr(research_stage, "batch_verify", batch_verify_stub)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_research_enriches_article_with_confirm(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pipeline(monkeypatch, batch_verify_stub=_stub_batch_verify_confirm)

    result = research_stage.run(settings, article_id="SCI-001")

    assert result.confirms >= 1
    assert result.claims_total == 1
    fm, body, _ = read_article(settings.CURATED_DIR / "Science" / "plate-tectonics.md")
    assert "wikipedia.org" in body
    dump = fm.model_dump()
    assert dump.get("research_claims_total") == 1


@pytest.mark.integration
def test_research_article_not_found(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pipeline(monkeypatch, batch_verify_stub=_stub_batch_verify_confirm)
    result = research_stage.run(settings, article_id="NONEXISTENT")
    assert result.findings == []
    assert result.claims_total == 0


@pytest.mark.integration
def test_research_with_dispute(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pipeline(monkeypatch, batch_verify_stub=_stub_batch_verify_dispute)

    result = research_stage.run(settings, article_id="SCI-001")

    assert result.disputes >= 1
    fm, body, _ = read_article(settings.CURATED_DIR / "Science" / "plate-tectonics.md")
    assert "## Disputes" in body
    dump = fm.model_dump()
    assert dump.get("dispute_count", 0) >= 1
