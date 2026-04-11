"""Grounding judge — aggregation only (no real LLM calls)."""

from __future__ import annotations

from evals.evaluators.generation.grounding_judge import (
    ClaimVerdict,
    GroundingBatch,
    GroundingJudge,
    claims_from_body,
)


def _verdict(idx: int, grounded: bool) -> ClaimVerdict:
    return ClaimVerdict(
        claim_index=idx,
        reasoning="stub",
        is_grounded=grounded,
        evidence_quote="" if not grounded else "quote",
    )


def test_aggregate_all_grounded_returns_one() -> None:
    batch = GroundingBatch(verdicts=[_verdict(0, True), _verdict(1, True)])
    assert GroundingJudge.aggregate(batch, expected=2)["eval_grounding_score"] == 1.0


def test_aggregate_partial_returns_fraction() -> None:
    batch = GroundingBatch(verdicts=[_verdict(0, True), _verdict(1, False)])
    assert GroundingJudge.aggregate(batch, expected=2)["eval_grounding_score"] == 0.5


def test_aggregate_empty_expected_returns_one() -> None:
    batch = GroundingBatch(verdicts=[])
    assert GroundingJudge.aggregate(batch, expected=0)["eval_grounding_score"] == 1.0


def test_claims_from_body_strips_blanks() -> None:
    claims = claims_from_body(["one", "", "  ", "two"])
    assert claims == ["one", "two"]
