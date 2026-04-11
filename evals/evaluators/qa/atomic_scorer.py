"""Deterministic gate: each Q&A pair contains exactly one question."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AtomicScorer:
    def score(self, question: str) -> dict[str, float | bool | int]:
        # Heuristic: a "?" in the middle plus more text after suggests a compound.
        question_marks = question.count("?")
        atomic = question_marks <= 1
        return {
            "qa_question_marks": float(question_marks),
            "qa_atomic_passed": atomic,
        }
