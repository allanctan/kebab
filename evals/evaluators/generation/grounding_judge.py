"""LLM-as-judge: are claims in a generated article grounded in cited sources?

Pattern: per-item batch verdict. The judge receives all claims in one
call and returns a list of per-claim verdicts. Aggregation is done in
Python (not by the LLM).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from app.config.config import Settings
from evals.evaluators.common import build_judge_agent

_SYSTEM_PROMPT = """You verify whether each claim in a curated article is grounded in cited sources.

## Input
- A list of (claim_index, claim_text) pairs.
- The list of source titles cited in the article frontmatter.
- A short snippet from each source.

## Output (`GroundingBatch`)
- `verdicts`: per-claim verdicts. Always emit one entry per input claim.

## Per-claim fields (in declaration order — write reasoning first):
- `claim_index`: zero-based index of the claim.
- `reasoning`: brief analysis tracing the claim to a specific source.
- `is_grounded`: true only if the source clearly supports the claim.
- `evidence_quote`: short quote from the source supporting the claim, or "" if none.

## GOOD example
Claim: "Plants use chloroplasts to capture light."
Verdict: is_grounded=true, reasoning="Source A says: 'Chloroplasts capture sunlight'.",
evidence_quote="Chloroplasts capture sunlight"

## BAD example
Claim: "Plants are sentient and dream at night."
Verdict: is_grounded=false, reasoning="No source mentions sentience.", evidence_quote=""
"""


class ClaimVerdict(BaseModel):
    """One per-claim verdict."""

    model_config = ConfigDict(extra="forbid")

    claim_index: int = Field(..., description="Zero-based index of the claim.")
    reasoning: str = Field(..., description="Brief analysis before the verdict.")
    is_grounded: bool = Field(..., description="True if the source supports the claim.")
    evidence_quote: str = Field(..., description="Short quote, or empty string.")


class GroundingBatch(BaseModel):
    """Top-level judge output: one verdict per input claim."""

    model_config = ConfigDict(extra="forbid")

    verdicts: list[ClaimVerdict] = Field(..., description="Per-claim verdicts.")


@dataclass
class GroundingJudge:
    settings: Settings

    def judge(
        self, claims: list[str], sources: list[tuple[str, str]]
    ) -> GroundingBatch:
        """Send all claims in one batch call. Returns the structured output."""
        agent = build_judge_agent(GroundingBatch, _SYSTEM_PROMPT, self.settings)
        sources_str = "\n\n".join(f"### {name}\n{snippet}" for name, snippet in sources)
        claims_str = "\n".join(f"{i}: {claim}" for i, claim in enumerate(claims))
        return agent.run_sync(f"sources:\n{sources_str}\n\nclaims:\n{claims_str}").output

    @staticmethod
    def aggregate(batch: GroundingBatch, expected: int) -> dict[str, float]:
        """Compute the grounding rate. Code does the math, never the LLM."""
        if expected == 0:
            return {"eval_grounding_score": 1.0}
        n_grounded = sum(1 for v in batch.verdicts if v.is_grounded)
        return {"eval_grounding_score": n_grounded / expected}


def claims_from_body(body: Iterable[str]) -> list[str]:
    """Naive claim extractor — one claim per non-empty line.

    Real eval suites can swap this for a smarter splitter; the deliberate
    contract is that **code** decides what counts as a claim, not the LLM.
    """
    return [line.strip() for line in body if line.strip()]
