"""Tests for research-based confidence computation."""

from __future__ import annotations

from app.core.confidence import compute_confidence
from app.models.frontmatter import FrontmatterSchema
from app.models.source import Source


def _fm(
    sources: int = 1,
    human_verified: bool = False,
    research_claims_total: int | None = None,
    external_confirms: int = 0,
    dispute_count: int = 0,
) -> FrontmatterSchema:
    fm = FrontmatterSchema(
        id="TEST-001",
        name="Test",
        type="article",
        sources=[Source(id=i, title=f"src-{i}", tier=2) for i in range(sources)],
        human_verified=human_verified,
    )
    dump = fm.model_dump()
    if research_claims_total is not None:
        dump["research_claims_total"] = research_claims_total
        dump["external_confirms"] = external_confirms
        dump["dispute_count"] = dispute_count
    return FrontmatterSchema.model_validate(dump)


class TestConfidenceV2:
    def test_level_0_no_sources(self) -> None:
        assert compute_confidence(_fm(sources=0)) == 0

    def test_level_1_has_sources_not_researched(self) -> None:
        assert compute_confidence(_fm(sources=2)) == 1

    def test_level_2_researched_below_threshold(self) -> None:
        assert compute_confidence(_fm(
            sources=2, research_claims_total=10, external_confirms=5, dispute_count=0
        )) == 2

    def test_level_2_researched_has_disputes(self) -> None:
        assert compute_confidence(_fm(
            sources=2, research_claims_total=10, external_confirms=8, dispute_count=1
        )) == 2

    def test_level_3_researched_above_threshold_no_disputes(self) -> None:
        assert compute_confidence(_fm(
            sources=2, research_claims_total=10, external_confirms=8, dispute_count=0
        )) == 3

    def test_level_3_exact_threshold(self) -> None:
        assert compute_confidence(_fm(
            sources=2, research_claims_total=10, external_confirms=7, dispute_count=0
        )) == 3

    def test_level_4_human_verified(self) -> None:
        assert compute_confidence(_fm(human_verified=True)) == 4

    def test_human_verified_overrides_disputes(self) -> None:
        assert compute_confidence(_fm(
            human_verified=True, research_claims_total=10, external_confirms=5, dispute_count=3
        )) == 4
