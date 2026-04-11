"""InjectionDetectionJudge is pure code — exhaustive truth table."""

from __future__ import annotations

from app.config.config import Settings
from evals.evaluators.verification.injection_detection_judge import (
    InjectionDetectionJudge,
)


def _judge() -> InjectionDetectionJudge:
    return InjectionDetectionJudge(Settings(GOOGLE_API_KEY="x"))


def test_injected_and_caught_is_detected() -> None:
    batch = _judge().judge([("a", True, False)])
    assert batch.verdicts[0].detected is True


def test_injected_and_missed_is_not_detected() -> None:
    batch = _judge().judge([("a", True, True)])
    assert batch.verdicts[0].detected is False


def test_clean_and_passed_is_correct() -> None:
    batch = _judge().judge([("a", False, True)])
    assert batch.verdicts[0].detected is True


def test_clean_and_flagged_is_wrong() -> None:
    batch = _judge().judge([("a", False, False)])
    assert batch.verdicts[0].detected is False


def test_aggregate_detection_rate() -> None:
    batch = _judge().judge(
        [
            ("a", True, False),  # correct
            ("b", True, True),   # missed
            ("c", False, True),  # correct
            ("d", False, False), # wrong
        ]
    )
    agg = InjectionDetectionJudge.aggregate(batch)
    assert agg["eval_detection_rate"] == 0.5
