"""Integration test for the research agent orchestrator."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.research.agent import run
from app.agents.research.executor import FindingResult
from app.agents.research.planner import ClaimEntry, ResearchPlan, SearchQuery
from app.config.config import Settings
from app.core.markdown import read_article, write_article
from app.core.sources.index import SourceIndex, SourceEntry, save_index
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
        sources=[SourceEntry(id=1, stem="test", raw_path="raw/documents/test.pdf",
                             title="Test Source", tier=1, checksum="abc", adapter="local_pdf")],
        next_id=2,
    )
    save_index(index, knowledge / ".kebab" / "sources.json")

    fm = FrontmatterSchema(
        id="SCI-001", name="Plate Tectonics", type="article",
        sources=[Source(id=1, title="Test Source", tier=1)],
    )
    body = (
        "# Plate Tectonics\n\n"
        "Plates move due to convection currents.\n\n"
        "[^1]: [1] [Test Source](../../raw/documents/test.pdf)\n"
    )
    write_article(curated / "plate-tectonics.md", fm, body)

    return Settings(
        KNOWLEDGE_DIR=knowledge, RAW_DIR=knowledge / "raw",
        PROCESSED_DIR=knowledge / "processed",
        CURATED_DIR=knowledge / "curated",
        QDRANT_PATH=None, QDRANT_URL=None, GOOGLE_API_KEY="test-key",
    )


def _stub_planner(_settings: Settings, _deps: object) -> ResearchPlan:
    return ResearchPlan(
        claims=[ClaimEntry(text="Plates move due to convection currents", section="Plate Tectonics", paragraph=1)],
        queries=[SearchQuery(query="plate tectonics convection", adapter="wikipedia", target_claims=[0])],
    )


def _stub_searcher(_adapter_name: str, _query: str, _settings: Settings) -> list[tuple[str, str, str]]:
    return [("Wikipedia: Plate tectonics", "https://en.wikipedia.org/wiki/Plate_tectonics",
             "Convection currents in the mantle drive plate movement.")]


def _stub_classifier(_settings: Settings, _claim: object, _source_title: str, _source_content: str) -> FindingResult:
    return FindingResult(
        outcome="confirm",
        reasoning="Source confirms convection drives plates.",
        evidence_quote="Convection currents in the mantle drive plate movement.",
    )


@pytest.mark.integration
def test_research_enriches_article_with_confirm(settings: Settings) -> None:
    result = run(
        settings,
        article_id="SCI-001",
        planner=_stub_planner,
        searcher=_stub_searcher,
        classifier=_stub_classifier,
    )
    assert result.confirms >= 1
    assert result.claims_total == 1
    fm, body = read_article(settings.CURATED_DIR / "Science" / "plate-tectonics.md")
    assert "wikipedia.org" in body
    dump = fm.model_dump()
    assert dump.get("research_claims_total") == 1


@pytest.mark.integration
def test_research_article_not_found(settings: Settings) -> None:
    result = run(settings, article_id="NONEXISTENT")
    assert result.findings == []
    assert result.claims_total == 0


def _stub_classifier_dispute(_settings: Settings, _claim: object, _source_title: str, _source_content: str) -> FindingResult:
    return FindingResult(
        outcome="dispute",
        reasoning="Source contradicts.",
        evidence_quote="Slab pull is dominant.",
        contradiction="Source says slab pull, not convection, is primary.",
    )


@pytest.mark.integration
def test_research_with_dispute(settings: Settings) -> None:
    result = run(
        settings,
        article_id="SCI-001",
        planner=_stub_planner,
        searcher=_stub_searcher,
        classifier=_stub_classifier_dispute,
    )
    assert result.disputes >= 1
    fm, body = read_article(settings.CURATED_DIR / "Science" / "plate-tectonics.md")
    assert "## Disputes" in body
    dump = fm.model_dump()
    assert dump.get("dispute_count", 0) >= 1
