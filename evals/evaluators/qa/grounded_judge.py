"""LLM-as-judge: are Q&A answers grounded in cited sources?"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.config.config import Settings
from evals.evaluators.common import build_judge_agent

_SYSTEM_PROMPT = """You judge whether Q&A answers are grounded in cited sources.

## Input
- A list of (pair_index, question, answer, source_titles) tuples.

## Output (`QaGroundedBatch`)
- `verdicts`: one verdict per input pair.

## Per-pair fields (in declaration order — write reasoning first):
- `pair_index`: zero-based index.
- `reasoning`: brief analysis of the link between answer and sources.
- `is_grounded`: true only if a cited source clearly supports the answer.

## GOOD example
Q: "Where does photosynthesis happen?"
A: "In chloroplasts." sources: ["OpenStax Biology 2e"]
Verdict: is_grounded=true, reasoning="OpenStax explicitly says chloroplasts."

## BAD example
Q: "What is the meaning of life?"
A: "42" sources: ["DepEd MELC"]
Verdict: is_grounded=false, reasoning="DepEd MELC does not discuss this."
"""


class QaGroundedVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_index: int = Field(..., description="Zero-based index.")
    reasoning: str = Field(..., description="Brief analysis before verdict.")
    is_grounded: bool = Field(..., description="True if grounded.")


class QaGroundedBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdicts: list[QaGroundedVerdict] = Field(..., description="One per input pair.")


@dataclass
class GroundedJudge:
    settings: Settings

    def judge(
        self, pairs: list[tuple[str, str, list[str]]]
    ) -> QaGroundedBatch:
        agent = build_judge_agent(QaGroundedBatch, _SYSTEM_PROMPT, self.settings)
        prompt = "\n\n".join(
            f"{i}: Q={q!r}, A={a!r}, sources={s!r}"
            for i, (q, a, s) in enumerate(pairs)
        )
        return agent.run_sync(prompt).output

    @staticmethod
    def aggregate(batch: QaGroundedBatch) -> dict[str, float]:
        if not batch.verdicts:
            return {"eval_grounded_score": 1.0}
        hits = sum(1 for v in batch.verdicts if v.is_grounded)
        return {"eval_grounded_score": hits / len(batch.verdicts)}
