"""Pilot walkthrough — full ingest → sync pipeline with stubbed LLMs.

Verifies the M16 acceptance criteria:
- Photosynthesis article reaches `confidence_level == 3` after verify+sync.
- The qa agent appends at least one new pair that does not duplicate.
- The lint agent reports no fatal issues for the pilot tree.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from qdrant_client import QdrantClient

from app.agents.lint import agent as lint_agent
from app.agents.qa import agent as qa_agent
from app.config.config import Settings
from app.core.markdown import read_article
from app.pipeline.organize.agent import HierarchyNode, HierarchyPlan
from app.core.store import EMBEDDING_DIM, Store
from app.models.source import Source
from app.pipeline.generate import contexts as contexts_stage
from app.pipeline.generate import gaps as gaps_stage
from app.pipeline.generate import writer as generate_stage
from app.pipeline import organize as organize_stage
from app.pipeline import sync as sync_stage
from app.pipeline.generate.gaps import Gap
from app.pipeline.ingest import pdf as pdf_ingest
from datetime import date


def _make_pdf(path: Path, body: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), body)
    doc.save(path)
    doc.close()


# ----- stub LLM helpers ------------------------------------------------------

def _extract_id(label: str) -> int | None:
    """Extract the source ID from a manifest label like ``[3] Title``."""
    import re
    m = re.match(r"^\[(\d+)\]", label)
    return int(m.group(1)) if m else None


def _stub_organize_proposer(
    _settings: Settings, _domain: str, manifest: list[tuple[str, str]]
) -> HierarchyPlan:
    source_ids = [sid for name, _ in manifest if (sid := _extract_id(name)) is not None]
    return HierarchyPlan(
        nodes=[
            HierarchyNode(id="SCI", name="Science", level_type="domain", description="x"),
            HierarchyNode(
                id="SCI-BIO",
                name="Biology",
                level_type="subdomain",
                parent_id="SCI",
                description="x",
            ),
            HierarchyNode(
                id="SCI-BIO-PHO",
                name="Photosynthesis",
                level_type="topic",
                parent_id="SCI-BIO",
                description="x",
            ),
            HierarchyNode(
                id="SCI-BIO-001",
                name="Photosynthesis",
                level_type="article",
                parent_id="SCI-BIO-PHO",
                description="Light into glucose.",
                source_files=source_ids,
            ),
        ]
    )


def _stub_generate(
    _settings: Settings, gap: Gap, sources: list[tuple[str, str]]
) -> generate_stage.GenerationResult:
    body = (
        f"# {gap.name}\n\n"
        "Plants convert light energy into chemical energy stored in glucose.[^1]\n"
        "Photosynthesis releases oxygen as a byproduct.[^2]\n"
    )
    # Cite both local footnote numbers that were passed in
    local_nums = list(range(1, len(sources) + 1)) if sources else [1]
    return generate_stage.GenerationResult(
        reasoning="OpenStax and DepEd both cover these claims.",
        body=body,
        description="Light into glucose.",
        keywords=["chloroplast", "calvin"],
        summary="Test scope.", source_ids=local_nums,
    )


def _stub_contexts_proposer(
    _settings: Settings, _deps: contexts_stage.ContextDeps, _cls: type
) -> contexts_stage.EducationContext:
    return contexts_stage.EducationContext(grade=7, subject="science", language="en")


def _stub_research_planner(_settings, _deps):
    from app.agents.research.planner import ClaimEntry, ResearchPlan, SearchQuery
    return ResearchPlan(
        claims=[ClaimEntry(text="test claim", section="Intro", paragraph=1)],
        queries=[SearchQuery(query="test", adapter="wikipedia", target_claims=[0])],
    )


def _stub_research_searcher(_adapter, _query, _settings):
    return [("Wikipedia: Test", "https://en.wikipedia.org/wiki/Test", "Test confirms the claim.")]


def _stub_research_classifier(_settings, _claim, _source_title, _source_content):
    from app.agents.research.executor import FindingResult
    return FindingResult(outcome="confirm", reasoning="confirmed", evidence_quote="Test confirms.")


def _stub_qa(_settings: Settings, deps: qa_agent.QaDeps) -> qa_agent.QaResult:
    return qa_agent.QaResult(
        reasoning="One non-duplicate question to add.",
        new_questions=[
            qa_agent.QaPair(
                question="How do chloroplasts capture light energy?",
                answer="Pigments in the thylakoid membrane absorb photons.",
                sources=[Source(id=0, title="OpenStax Biology 2e", tier=2)],
            )
        ],
        is_ready_to_commit=True,
    )


def _stub_embed(texts: list[str], _settings: Settings) -> list[list[float]]:
    return [[(i + 1) * 0.001] * EMBEDDING_DIM for i in range(len(texts))]


# ----- pilot test ------------------------------------------------------------


@pytest.mark.integration
def test_pilot_end_to_end(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    raw_docs = knowledge / "raw" / "documents"
    raw_docs.mkdir(parents=True)

    # Stage 0 — drop two real-looking PDFs and ingest them.
    pdf_a = tmp_path / "openstax_biology_2e_chapter_8.pdf"
    pdf_b = tmp_path / "deped_k12_science_grade7.pdf"
    _make_pdf(pdf_a, "Photosynthesis converts light into glucose.")
    _make_pdf(pdf_b, "Plants release oxygen during photosynthesis.")

    settings = Settings(
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        PROCESSED_DIR=knowledge / "processed",
        CURATED_DIR=knowledge / "curated",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
        LLM_VERIFICATION_MODELS=["google-gla:gemini-2.5-flash", "google-gla:gemini-2.5-flash-lite"],
    )
    store = Store(settings, client=QdrantClient(":memory:"))

    # Fake describer (no real multimodal calls during test).
    def _fake_describer(*_args: object, **_kwargs: object) -> str:
        return "DECORATIVE"

    pdf_ingest.ingest(settings, pdf_a, describer=_fake_describer)
    pdf_ingest.ingest(settings, pdf_b, describer=_fake_describer)

    # Stage 1 — organize creates the canonical plan + empty stubs.
    organize_stage.run(settings, domain_hint="Science", proposer=_stub_organize_proposer)

    # Stage 2 — gaps diffs the plan against the (empty) index.
    gaps_result = gaps_stage.run(settings, domain="Science", store=store)
    assert len(gaps_result.report.gaps) == 1
    assert gaps_result.report.gaps[0].target_path is not None

    # Stage 3 — generate writes directly to the plan-reserved path.
    generate_result = generate_stage.write_articles(settings, domain="Science", gaps=gaps_result.report, proposer=_stub_generate)
    assert len(generate_result.written) == 1
    # The path matches exactly what organize reserved — no parallel trees.
    assert str(generate_result.written[0]) == gaps_result.report.gaps[0].target_path

    # Stage 5 — contexts adds the K-12 grade.
    contexts_stage.run(settings, proposer=_stub_contexts_proposer)

    # Stage 6 — research agent confirms claims via external sources → confidence ≥ 3.
    from app.agents.research.agent import run as research_run
    article_paths = list(settings.CURATED_DIR.rglob("*.md"))
    assert len(article_paths) == 1
    fm_pre, _ = read_article(article_paths[0])
    research_run(
        settings,
        article_id=fm_pre.id,
        planner=_stub_research_planner,
        searcher=_stub_research_searcher,
        classifier=_stub_research_classifier,
    )

    # Confirm the markdown frontmatter reflects research metadata.
    fm, body = read_article(article_paths[0])
    assert fm.model_dump().get("research_claims_total") == 1

    # Stage 7 — sync into Qdrant. Confidence histogram should show level 3.
    sync_result = sync_stage.run(settings, store=store, embed_fn=_stub_embed)
    assert sync_result.articles == 1
    assert sync_result.confidence_histogram.get(3, 0) == 1

    # Q&A agent — at least one new pair appended.
    qa_result = qa_agent.run(settings, once=True, proposer=_stub_qa)
    assert qa_result.pairs_added >= 1
    fm_after, body_after = read_article(article_paths[0])
    assert "How do chloroplasts" in body_after

    # Lint agent — no orphans (article has parent_ids), gate is met.
    lint_result = lint_agent.run(settings, store=store, today=lambda: date(2026, 4, 9))
    issue_codes = {i.code for i in lint_result.report.issues}
    assert "below_confidence_gate" not in issue_codes
    assert "missing_sources" not in issue_codes
