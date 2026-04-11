"""LLM-as-judge: did the verifier catch deliberately injected errors?

Inputs: a list of articles, each with a known injected error and the
verifier's actual outcome. The judge does not need to read the article
itself — it just compares the verifier's verdict to the ground truth and
emits a per-item ``detected`` boolean. Aggregation is done in Python.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.config.config import Settings


class DetectionVerdict(BaseModel):
    """One per-article detection verdict."""

    model_config = ConfigDict(extra="forbid")

    article_id: str = Field(..., description="The article being judged.")
    reasoning: str = Field(..., description="Brief analysis.")
    detected: bool = Field(..., description="True if the verifier flagged the injection.")


class DetectionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdicts: list[DetectionVerdict] = Field(..., description="One verdict per article.")


@dataclass
class InjectionDetectionJudge:
    """Pure-code judge — no LLM call needed for the trivial comparison."""

    settings: Settings

    def judge(
        self,
        cases: list[tuple[str, bool, bool]],  # (article_id, injected, verifier_passed)
    ) -> DetectionBatch:
        """Detection succeeds when an injected error → verifier failed."""
        verdicts: list[DetectionVerdict] = []
        for article_id, injected, verifier_passed in cases:
            if injected:
                detected = not verifier_passed
                reasoning = (
                    "verifier correctly flagged injected error"
                    if detected
                    else "verifier missed the injected error"
                )
            else:
                detected = verifier_passed
                reasoning = (
                    "verifier passed clean article"
                    if detected
                    else "verifier wrongly flagged a clean article"
                )
            verdicts.append(
                DetectionVerdict(
                    article_id=article_id,
                    reasoning=reasoning,
                    detected=detected,
                )
            )
        return DetectionBatch(verdicts=verdicts)

    @staticmethod
    def aggregate(batch: DetectionBatch) -> dict[str, float]:
        if not batch.verdicts:
            return {"eval_detection_rate": 0.0}
        hits = sum(1 for v in batch.verdicts if v.detected)
        return {"eval_detection_rate": hits / len(batch.verdicts)}
