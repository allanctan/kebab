"""LLM-as-judge: are Q&A pairs *useful* (1–5 scale)?

Usefulness is a diagnostic, not a gate — noisier than grounded_judge but
helps spot trivial or off-topic enrichments.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.config.config import Settings
from evals.evaluators.common import build_judge_agent

_SYSTEM_PROMPT = """You rate the usefulness of Q&A pairs on a 1–5 scale.

Useful Q&A pairs:
- Cover non-obvious questions a learner would ask.
- Have answers that teach something, not just restate the question.
- Avoid duplicating the article body verbatim.

## Input
- A list of (pair_index, question, answer) tuples.

## Output (`QaUsefulnessBatch`)
- `verdicts`: one per pair.

## Per-pair fields (write reasoning first):
- `pair_index`
- `reasoning`
- `score`: integer 1–5

## GOOD example
Q: "Why is photosynthesis important for animal life?"
A: "Animals depend on plants for the oxygen photosynthesis releases."
Verdict: score=5, reasoning="Bridges plants and animals — non-obvious."

## BAD example
Q: "What is photosynthesis?"
A: "Photosynthesis is photosynthesis."
Verdict: score=1, reasoning="Tautological, teaches nothing."
"""


class QaUsefulnessVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_index: int = Field(..., description="Zero-based index.")
    reasoning: str = Field(..., description="Brief analysis before verdict.")
    score: int = Field(..., ge=1, le=5, description="1=useless, 5=excellent.")


class QaUsefulnessBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdicts: list[QaUsefulnessVerdict] = Field(..., description="One per input pair.")


@dataclass
class UsefulnessJudge:
    settings: Settings

    def judge(self, pairs: list[tuple[str, str]]) -> QaUsefulnessBatch:
        agent = build_judge_agent(QaUsefulnessBatch, _SYSTEM_PROMPT, self.settings)
        prompt = "\n\n".join(
            f"{i}: Q={q!r}, A={a!r}" for i, (q, a) in enumerate(pairs)
        )
        return agent.run_sync(prompt).output

    @staticmethod
    def aggregate(batch: QaUsefulnessBatch) -> dict[str, float]:
        if not batch.verdicts:
            return {"eval_usefulness_score": 0.0}
        avg = sum(v.score for v in batch.verdicts) / len(batch.verdicts)
        return {"eval_usefulness_score": avg}
