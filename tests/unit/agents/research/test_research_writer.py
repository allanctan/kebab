"""Tests for the research writer + verifier model classes.

Originally ``test_research_executor.py``. Renamed when ``executor.py`` was
split into ``verifier.py`` (LLM agents) and ``writer.py`` (markdown surgery)
in the 2026-04-12 research restructure.
"""

from __future__ import annotations

from app.agents.research.planner import ClaimEntry
from app.agents.research.verifier import (
    DisputeJudgment,
    FindingResult,
    FindingTuple,
)
from app.agents.research.writer import apply_findings_to_article


class TestFindingResult:
    def test_confirm_finding(self) -> None:
        f = FindingResult(
            outcome="confirm",
            reasoning="Source agrees.",
            evidence_quote="Plates move due to convection.",
        )
        assert f.outcome == "confirm"
        assert f.new_sentence is None

    def test_append_finding(self) -> None:
        f = FindingResult(
            outcome="append",
            reasoning="New info.",
            evidence_quote="Ridge push also contributes.",
            new_sentence="Ridge push at mid-ocean ridges also contributes to plate movement.",
        )
        assert f.outcome == "append"
        assert f.new_sentence is not None

    def test_dispute_finding(self) -> None:
        f = FindingResult(
            outcome="dispute",
            reasoning="Source contradicts.",
            evidence_quote="Slab pull is dominant.",
            contradiction="Source says slab pull, not convection, is the primary driver.",
        )
        assert f.outcome == "dispute"
        assert f.contradiction is not None


class TestDisputeJudgment:
    def test_factual_error_is_surfaced(self) -> None:
        j = DisputeJudgment(category="factual_error", reasoning="Demonstrably false.", summary="Pluto is not a planet.")
        assert j.is_surfaced is True
        assert j.category == "factual_error"

    def test_misleading_simplification_is_surfaced(self) -> None:
        j = DisputeJudgment(category="misleading_simplification", reasoning="Wrong mechanism.", summary="Plate melts vs mantle melts.")
        assert j.is_surfaced is True

    def test_contested_is_surfaced(self) -> None:
        j = DisputeJudgment(category="contested_or_opinion", reasoning="Experts disagree.", summary="Competing models.")
        assert j.is_surfaced is True

    def test_acceptable_simplification_is_not_surfaced(self) -> None:
        j = DisputeJudgment(category="acceptable_simplification", reasoning="Grade-appropriate.")
        assert j.is_surfaced is False

    def test_false_positive_is_not_surfaced(self) -> None:
        j = DisputeJudgment(category="false_positive", reasoning="Phrasing difference.")
        assert j.is_surfaced is False

    def test_normalizes_category_casing(self) -> None:
        j = DisputeJudgment(category="FACTUAL_ERROR", reasoning="test")  # type: ignore[arg-type]
        assert j.category == "factual_error"

    def test_normalizes_spaces_to_underscores(self) -> None:
        j = DisputeJudgment(category="factual error", reasoning="test")  # type: ignore[arg-type]
        assert j.category == "factual_error"


class TestApplyFindings:
    def _base_body(self) -> str:
        return "# Topic\n\nExisting content about plates.\n\n[^1]: [1] [Local](path.pdf)\n"

    def test_confirm_adds_footnote(self) -> None:
        body = self._base_body()
        findings: list[FindingTuple] = [
            (
                ClaimEntry(text="Existing content about plates", section="Topic", paragraph=1),
                FindingResult(outcome="confirm", reasoning="agrees", evidence_quote="Same fact."),
                "Wikipedia: Topic",
                "https://en.wikipedia.org/wiki/Topic",
            ),
        ]
        result = apply_findings_to_article(body, findings)
        assert "[^2]" in result
        assert "https://en.wikipedia.org/wiki/Topic" in result

    def test_append_adds_sentence_with_footnote(self) -> None:
        body = self._base_body()
        findings: list[FindingTuple] = [
            (
                ClaimEntry(text="Existing content about plates", section="Topic", paragraph=1),
                FindingResult(
                    outcome="append", reasoning="new info", evidence_quote="quote",
                    new_sentence="New fact from external source.",
                ),
                "Wikipedia: Topic",
                "https://en.wikipedia.org/wiki/Topic",
            ),
        ]
        result = apply_findings_to_article(body, findings)
        assert "New fact from external source." in result
        assert "[^2]" in result

    def test_dispute_adds_disputes_section(self) -> None:
        body = self._base_body()
        findings: list[FindingTuple] = [
            (
                ClaimEntry(text="Existing content about plates", section="Topic", paragraph=1),
                FindingResult(
                    outcome="dispute", reasoning="contradicts", evidence_quote="Opposite.",
                    contradiction="Source says the opposite.",
                ),
                "Wikipedia: Topic",
                "https://en.wikipedia.org/wiki/Topic",
            ),
        ]
        result = apply_findings_to_article(body, findings)
        assert "## Disputes" in result
        assert "**Claim**:" in result
        assert "**Category**: Dispute" in result  # default when no dispute_category set
        assert "Source says the opposite." in result

    def test_empty_findings_returns_body_unchanged(self) -> None:
        body = self._base_body()
        result = apply_findings_to_article(body, [])
        assert result.strip() == body.strip()

    def test_mixed_findings(self) -> None:
        body = "# Science\n\nFact one about rocks. Fact two about plates.\n\n[^1]: [1] [Src](p.pdf)\n"
        findings: list[FindingTuple] = [
            (
                ClaimEntry(text="Fact one about rocks", section="Science", paragraph=1),
                FindingResult(outcome="confirm", reasoning="ok", evidence_quote="rocks"),
                "Wiki: Rocks", "https://en.wikipedia.org/wiki/Rocks",
            ),
            (
                ClaimEntry(text="Fact two about plates", section="Science", paragraph=1),
                FindingResult(
                    outcome="dispute", reasoning="wrong", evidence_quote="no",
                    contradiction="Plates don't work that way.",
                ),
                "Wiki: Plates", "https://en.wikipedia.org/wiki/Plates",
            ),
        ]
        result = apply_findings_to_article(body, findings)
        assert "[^2]" in result  # confirm footnote
        assert "## Disputes" in result  # dispute section
        assert "Plates don't work that way." in result
