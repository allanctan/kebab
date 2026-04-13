"""Confidence ladder transitions per spec §7."""

from __future__ import annotations

from datetime import date

from app.core.confidence import compute_confidence
from app.models.confidence import VerificationRecord
from app.models.frontmatter import FrontmatterSchema
from app.models.source import Source

TODAY = date(2026, 4, 9)


def _fm(**kw: object) -> FrontmatterSchema:
    base: dict[str, object] = {
        "id": "X-1",
        "name": "X",
        "type": "article",
        "sources": [],
        "verifications": [],
        "human_verified": False,
    }
    base.update(kw)
    return FrontmatterSchema.model_validate(base)


def _source(title: str = "src") -> Source:
    return Source(id=0, title=title, tier=2)


def _verif(passed: bool, model: str = "google-gla:gemini-2.5-flash") -> VerificationRecord:
    return VerificationRecord(model=model, passed=passed, date=TODAY)


def test_level_0_when_no_sources() -> None:
    assert compute_confidence(_fm()) == 0


def test_level_1_when_sources_but_no_verifications() -> None:
    assert compute_confidence(_fm(sources=[_source()])) == 1


def test_level_1_when_only_failed_verifications() -> None:
    assert (
        compute_confidence(_fm(sources=[_source()], verifications=[_verif(False)])) == 1
    )


def test_level_2_when_one_verifier_passed() -> None:
    assert (
        compute_confidence(_fm(sources=[_source()], verifications=[_verif(True)])) == 2
    )


def test_level_2_when_two_verifiers_but_only_one_source() -> None:
    fm = _fm(
        sources=[_source()],
        verifications=[_verif(True, "a"), _verif(True, "b")],
    )
    assert compute_confidence(fm) == 2


def test_level_3_when_two_verifiers_and_two_sources() -> None:
    fm = _fm(
        sources=[_source("a"), _source("b")],
        verifications=[_verif(True, "m1"), _verif(True, "m2")],
    )
    assert compute_confidence(fm) == 3


def test_level_4_when_human_verified_overrides_everything() -> None:
    assert compute_confidence(_fm(human_verified=True)) == 4
    assert compute_confidence(_fm(sources=[_source()], human_verified=True)) == 4


class TestResearchBasedConfidence:
    """Tests for research-based verification (external source confirmation)."""

    def test_level_3_with_research_high_confirmation_no_disputes(self) -> None:
        """Article with 90% confirmation and 0 disputes reaches level 3."""
        fm = _fm(
            sources=[_source(f"src-{i}") for i in range(2)],
            research_claims_total=20,
            external_confirms=18,  # 90% > 70% threshold
            dispute_count=0,
        )
        assert compute_confidence(fm) == 3

    def test_level_2_with_research_low_confirmation(self) -> None:
        """Article with 50% confirmation stays at level 2."""
        fm = _fm(
            sources=[_source(f"src-{i}") for i in range(2)],
            research_claims_total=20,
            external_confirms=10,  # 50% < 70% threshold
            dispute_count=0,
        )
        assert compute_confidence(fm) == 2

    def test_level_2_with_research_resolvable_disputes(self) -> None:
        """Article with resolvable disputes stays at level 2 despite high confirmation."""
        fm = _fm(
            sources=[_source(f"src-{i}") for i in range(2)],
            research_claims_total=20,
            external_confirms=18,  # 90% > 70%
            dispute_count=1,  # Has resolvable dispute
        )
        assert compute_confidence(fm) == 2

    def test_level_3_unresolvable_disputes_do_not_block(self) -> None:
        """Articles with only unresolvable disputes can reach level 3.

        Since Task 3 ensures unresolvable_dispute_count is tracked separately
        from dispute_count, compute_confidence ignores unresolvable_dispute_count
        and only checks dispute_count (resolvable disputes).
        """
        fm = _fm(
            sources=[_source(f"src-{i}") for i in range(2)],
            research_claims_total=20,
            external_confirms=18,  # 90% > 70% threshold
            dispute_count=0,  # No resolvable disputes
            unresolvable_dispute_count=2,  # Has unresolvable disputes (doesn't block)
        )
        assert compute_confidence(fm) == 3

    def test_level_2_with_both_resolvable_and_unresolvable_disputes(self) -> None:
        """Article with resolvable disputes blocks level 3, regardless of unresolvable."""
        fm = _fm(
            sources=[_source(f"src-{i}") for i in range(2)],
            research_claims_total=20,
            external_confirms=18,  # 90% > 70%
            dispute_count=1,  # Has resolvable dispute
            unresolvable_dispute_count=1,  # Also has unresolvable disputes
        )
        assert compute_confidence(fm) == 2

    def test_level_2_at_exactly_70_percent_confirmation(self) -> None:
        """Article at exactly 70% confirmation stays at level 2 (threshold is >=70%)."""
        fm = _fm(
            sources=[_source(f"src-{i}") for i in range(2)],
            research_claims_total=10,
            external_confirms=7,  # 70.0% == threshold
            dispute_count=0,
        )
        # The code uses ratio >= _CONFIRM_THRESHOLD (0.70), so 70% should reach level 3
        assert compute_confidence(fm) == 3

    def test_level_2_just_below_70_percent_confirmation(self) -> None:
        """Article below 70% confirmation stays at level 2."""
        fm = _fm(
            sources=[_source(f"src-{i}") for i in range(2)],
            research_claims_total=10,
            external_confirms=6,  # 60% < 70% threshold
            dispute_count=0,
        )
        assert compute_confidence(fm) == 2
