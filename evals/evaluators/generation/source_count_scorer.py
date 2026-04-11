"""Deterministic gate: every generated article must cite at least one source."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SourceCountScorer:
    """Pass/fail check on the number of cited sources."""

    minimum: int = 1

    def score(self, sources: list[object]) -> dict[str, float | bool | int]:
        passed = len(sources) >= self.minimum
        return {
            "source_count": float(len(sources)),
            "source_count_passed": passed,
        }
