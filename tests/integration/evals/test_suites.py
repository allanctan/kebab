"""End-to-end suite runs with stubbed LLM judges."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.config.config import Settings
from evals.evaluators.generation.grounding_judge import (
    ClaimVerdict,
    GroundingBatch,
)
from evals.evaluators.qa.grounded_judge import (
    QaGroundedBatch,
    QaGroundedVerdict,
)
from evals.evaluators.qa.usefulness_judge import (
    QaUsefulnessBatch,
    QaUsefulnessVerdict,
)
from evals.run import compare_to_baseline
from evals.suites import generation, qa, verification


def _now() -> datetime:
    return datetime(2026, 4, 9, 12, 0, 0)


@pytest.fixture
def settings() -> Settings:
    return Settings(GOOGLE_API_KEY="test-key")


def _all_grounded(claims: list[str], _sources) -> GroundingBatch:
    return GroundingBatch(
        verdicts=[
            ClaimVerdict(claim_index=i, reasoning="ok", is_grounded=True, evidence_quote="q")
            for i, _ in enumerate(claims)
        ]
    )


def _all_grounded_qa(pairs) -> QaGroundedBatch:
    return QaGroundedBatch(
        verdicts=[
            QaGroundedVerdict(pair_index=i, reasoning="ok", is_grounded=True)
            for i, _ in enumerate(pairs)
        ]
    )


def _high_usefulness(pairs) -> QaUsefulnessBatch:
    return QaUsefulnessBatch(
        verdicts=[
            QaUsefulnessVerdict(pair_index=i, reasoning="ok", score=4)
            for i, _ in enumerate(pairs)
        ]
    )


@pytest.mark.integration
def test_generation_suite_passes_baseline_with_stub(settings: Settings) -> None:
    result = generation.run(settings, judge=_all_grounded, now=_now)
    assert result.aggregate["eval_grounding_score"] == 1.0
    assert result.output_path.exists()
    check = compare_to_baseline("generation", result.aggregate)
    assert check.passed, check.failures


@pytest.mark.integration
def test_verification_suite_writes_results(settings: Settings) -> None:
    result = verification.run(settings, now=_now)
    assert result.output_path.exists()
    # Dataset has 5 cases, 3 correct → eval_detection_rate = 0.6.
    assert result.aggregate["eval_detection_rate"] == pytest.approx(0.6)
    # Baseline floor is 0.5 → passes with headroom.
    check = compare_to_baseline("verification", result.aggregate)
    assert check.passed, check.failures


@pytest.mark.integration
def test_qa_suite_passes_baseline_with_stubs(settings: Settings) -> None:
    result = qa.run(
        settings,
        grounded_fn=_all_grounded_qa,
        usefulness_fn=_high_usefulness,
        now=_now,
    )
    assert result.aggregate["eval_grounded_score"] >= 0.95
    assert result.aggregate["eval_usefulness_score"] >= 3.5
    assert result.output_path.exists()
    check = compare_to_baseline("qa", result.aggregate)
    assert check.passed, check.failures
