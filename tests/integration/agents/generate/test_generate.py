"""Generate stage with stubbed proposer (no real LLM calls)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config.config import Settings
from app.core.errors import KebabError
from app.core.markdown import read_article
from app.core.sources.index import SourceEntry, SourceIndex, save_index
from app.agents.generate import writer as generate_stage
from app.agents.generate.gaps import Gap, GapReport


def _gap(id: str = "SCI-BIO-001", target_path: str | None = None) -> Gap:
    return Gap(
        id=id,
        name="Photosynthesis",
        description="Light into glucose.",
        source_files=[1],
        target_path=target_path,
    )


def _good_proposer(
    _settings: Settings, gap: Gap, sources: list[tuple[str, str]]
) -> generate_stage.GenerationResult:
    return generate_stage.GenerationResult(
        reasoning="Source 1 covers all claims.",
        body=f"# {gap.name}\n\nGrounded in sources.[^1]\n",
        description="Light into glucose.",
        keywords=["chloroplast", "calvin"],
        summary="Test scope summary.", source_ids=[1],
    )


def _huge_body_proposer(
    _settings: Settings, gap: Gap, sources: list[tuple[str, str]]
) -> generate_stage.GenerationResult:
    return generate_stage.GenerationResult(
        reasoning="ok",
        body="huge " * 80_000,
        description="x",
        keywords=[],
        summary="Test scope summary.", source_ids=[1],
    )


def _empty_sources_proposer(
    _settings: Settings, gap: Gap, sources: list[tuple[str, str]]
) -> generate_stage.GenerationResult:
    # Force the schema-level guarantee.
    return generate_stage.GenerationResult.model_validate(
        {
            "reasoning": "no sources",
            "body": "x",
            "description": "x",
            "keywords": [],
            "source_ids": [],
        }
    )


def _setup_source_index(knowledge_dir: Path) -> None:
    index = SourceIndex(
        sources=[
            SourceEntry(
                id=1,
                stem="openstax",
                raw_path="raw/documents/openstax.pdf",
                title="OpenStax Biology 2e",
                tier=2,
                checksum="abc",
                adapter="local_pdf",
            )
        ],
        next_id=2,
    )
    save_index(index, knowledge_dir / ".kebab" / "sources.json")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    processed = knowledge / "processed" / "documents" / "openstax"
    processed.mkdir(parents=True)
    (processed / "text.md").write_text("Photosynthesis text", encoding="utf-8")
    _setup_source_index(knowledge)
    return Settings(
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        PROCESSED_DIR=knowledge / "processed",
        CURATED_DIR=knowledge / "curated",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )


@pytest.mark.integration
def test_generate_writes_article_with_grounded_sources(settings: Settings) -> None:
    target = settings.CURATED_DIR / "Science" / "Biology" / "photosynthesis.md"
    report = GapReport(gaps=[_gap(target_path=str(target))])
    result = generate_stage.write_articles(settings, gaps=report, proposer=_good_proposer)
    assert result.written == [target]
    assert result.skipped == []
    fm, body = read_article(target)
    assert fm.id == "SCI-BIO-001"
    assert len(fm.sources) == 1
    assert fm.sources[0].id == 1
    assert "Photosynthesis" in body
    # Footnote definitions should be appended
    assert "[^1]:" in body


@pytest.mark.integration
def test_generate_skips_when_no_source_files_present(settings: Settings) -> None:
    gap = Gap(
        id="X-1",
        name="x",
        description="x",
        source_files=[999],  # ID not in index
        target_path=str(settings.CURATED_DIR / "X" / "x.md"),
    )
    report = GapReport(gaps=[gap])
    result = generate_stage.write_articles(settings, gaps=report, proposer=_good_proposer)
    assert result.written == []
    assert result.skipped[0][0] == "X-1"
    assert "no source" in result.skipped[0][1]


@pytest.mark.integration
def test_generate_skips_oversized_body(settings: Settings) -> None:
    target = settings.CURATED_DIR / "Science" / "Biology" / "photosynthesis.md"
    report = GapReport(gaps=[_gap(target_path=str(target))])
    result = generate_stage.write_articles(settings, gaps=report, proposer=_huge_body_proposer)
    assert result.written == []
    assert "tokens" in result.skipped[0][1]


@pytest.mark.integration
def test_generation_result_rejects_empty_sources() -> None:
    with pytest.raises(ValidationError):
        _empty_sources_proposer(None, _gap(), [])  # type: ignore[arg-type]


@pytest.mark.integration
def test_generate_raises_when_no_gaps_report(settings: Settings) -> None:
    with pytest.raises(KebabError):
        generate_stage.write_articles(settings, proposer=_good_proposer)


@pytest.mark.integration
def test_generate_overwrites_stub_at_plan_path(settings: Settings) -> None:
    """Generate writes exactly to the path the plan reserved, overwriting the stub."""
    biology = settings.KNOWLEDGE_DIR / "Science" / "Biology"
    biology.mkdir(parents=True)
    stub = biology / "light-reactions.md"
    stub.write_text(
        "---\nid: SCI-BIO-001\nname: Photosynthesis\ntype: article\nsources: []\n---\n\nstub\n",
        encoding="utf-8",
    )
    report = GapReport(gaps=[_gap(target_path=str(stub))])
    result = generate_stage.write_articles(settings, gaps=report, proposer=_good_proposer)
    assert result.written == [stub]
    assert "Grounded in" in stub.read_text()


@pytest.mark.integration
def test_generate_stamps_parent_ids_and_sources_from_index(settings: Settings) -> None:
    """Generated frontmatter carries sources from index and parent_ids chain."""
    from app.agents.organize.agent import HierarchyNode, HierarchyPlan
    from app.core.sources.index import SourceEntry, SourceIndex, save_index

    # Add a second source to the index
    index = SourceIndex(
        sources=[
            SourceEntry(
                id=1,
                stem="openstax",
                raw_path="raw/documents/openstax.pdf",
                title="OpenStax Biology 2e",
                tier=2,
                checksum="abc",
                adapter="local_pdf",
            ),
            SourceEntry(
                id=2,
                stem="deped",
                raw_path="raw/documents/deped.pdf",
                title="DepEd Grade 7",
                tier=1,
                checksum="def",
                adapter="local_pdf",
            ),
        ],
        next_id=3,
    )
    save_index(index, settings.KNOWLEDGE_DIR / ".kebab" / "sources.json")
    # Create processed text for source 2
    deped_dir = Path(settings.PROCESSED_DIR) / "documents" / "deped"
    deped_dir.mkdir(parents=True, exist_ok=True)
    (deped_dir / "text.md").write_text("Respiration text", encoding="utf-8")

    plan = HierarchyPlan(
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
                source_files=[1, 2],
            ),
        ]
    )
    target = settings.CURATED_DIR / "Science" / "Biology" / "photosynthesis.md"
    gap = Gap(
        id="SCI-BIO-001",
        name="Photosynthesis",
        description="light into glucose",
        source_files=[1, 2],
        target_path=str(target),
    )

    def _two_source_proposer(
        _settings: Settings, _gap: Gap, sources: list[tuple[str, str]]
    ) -> generate_stage.GenerationResult:
        return generate_stage.GenerationResult(
            reasoning="Both sources used.",
            body=f"# {_gap.name}\n\nGrounded.[^1][^2]\n",
            description="light into glucose.",
            keywords=["chloroplast"],
            summary="Test scope.",
            source_ids=[1, 2],
        )

    report = GapReport(gaps=[gap])
    result = generate_stage.write_articles(
        settings, gaps=report, proposer=_two_source_proposer, plan=plan
    )
    assert result.written == [target]

    fm, body = read_article(target)
    dump = fm.model_dump()
    source_ids = {s["id"] for s in dump["sources"]}
    assert source_ids == {1, 2}
    # parent_ids chain up from immediate parent to root.
    assert dump["parent_ids"] == ["SCI-BIO", "SCI"]
    # Footnote definitions appended
    assert "[^1]:" in body
    assert "[^2]:" in body


@pytest.mark.integration
def test_generate_preserves_verifications_on_regen(settings: Settings) -> None:
    """Regenerating a stale article must not nuke existing verifications."""
    target = settings.CURATED_DIR / "Science" / "Biology" / "photosynthesis.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "---\n"
        "id: SCI-BIO-001\n"
        "name: Photosynthesis\n"
        "type: article\n"
        "sources: []\n"
        "verifications:\n"
        "  - date: 2026-01-15\n"
        "    model: gemini-2.5-pro\n"
        "    passed: true\n"
        "human_verified: true\n"
        "human_verified_by: daisy\n"
        "human_verified_at: 2026-01-20\n"
        "---\n\nold body\n",
        encoding="utf-8",
    )
    report = GapReport(gaps=[_gap(target_path=str(target))])
    result = generate_stage.write_articles(settings, gaps=report, proposer=_good_proposer)
    assert result.written == [target]
    fm, _ = read_article(target)
    dump = fm.model_dump()
    assert dump["human_verified"] is True
    assert dump["human_verified_by"] == "daisy"
    assert len(dump["verifications"]) == 1
