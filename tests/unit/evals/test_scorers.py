"""Deterministic scorer behavior."""

from __future__ import annotations

from evals.evaluators.generation.source_count_scorer import SourceCountScorer
from evals.evaluators.generation.token_limit_scorer import TokenLimitScorer
from evals.evaluators.qa.atomic_scorer import AtomicScorer


def test_source_count_passes_with_one_source() -> None:
    result = SourceCountScorer().score(["one"])
    assert result["source_count"] == 1
    assert result["source_count_passed"] is True


def test_source_count_fails_with_no_sources() -> None:
    result = SourceCountScorer().score([])
    assert result["source_count_passed"] is False


def test_token_limit_passes_under_limit() -> None:
    result = TokenLimitScorer(limit=100).score("a few words")
    assert result["body_tokens_passed"] is True


def test_token_limit_fails_over_limit() -> None:
    result = TokenLimitScorer(limit=2).score("a b c d e f g")
    assert result["body_tokens_passed"] is False


def test_atomic_scorer_passes_single_question() -> None:
    result = AtomicScorer().score("What is photosynthesis?")
    assert result["qa_atomic_passed"] is True


def test_atomic_scorer_fails_compound_question() -> None:
    result = AtomicScorer().score("What is it? And why does it matter?")
    assert result["qa_atomic_passed"] is False
