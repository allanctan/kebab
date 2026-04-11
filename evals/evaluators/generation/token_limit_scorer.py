"""Deterministic gate: generated article body must fit ``MAX_TOKENS_PER_ARTICLE``."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.llm.tokens import count_tokens


@dataclass
class TokenLimitScorer:
    limit: int = 50_000

    def score(self, body: str) -> dict[str, float | bool | int]:
        n = count_tokens(body)
        return {
            "body_tokens": float(n),
            "body_tokens_passed": n <= self.limit,
        }
