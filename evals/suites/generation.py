"""Generation eval suite.

Cost budget: ~$0.02 per run with gemini-flash.

Runs deterministic scorers first (source_count, token_limit) and only
calls the grounding judge for cases that pass the structural gate. The
LLM does per-claim verdicts; aggregation is in Python.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from app.config.config import Settings
from evals.evaluators.generation.grounding_judge import (
    GroundingBatch,
    GroundingJudge,
    claims_from_body,
)
from evals.evaluators.generation.source_count_scorer import SourceCountScorer
from evals.evaluators.generation.token_limit_scorer import TokenLimitScorer

DATASET = Path(__file__).resolve().parent.parent / "datasets" / "generation.yaml"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results" / "generation"


@dataclass
class GenerationCaseResult:
    case_id: str
    label: str
    source_count_passed: bool
    body_tokens: float
    body_tokens_passed: bool
    eval_grounding_score: float | None


@dataclass
class GenerationSuiteResult:
    cases: list[GenerationCaseResult]
    aggregate: dict[str, float]
    output_path: Path


JudgeFn = Callable[[list[str], list[tuple[str, str]]], GroundingBatch]


def _load_dataset() -> list[dict[str, Any]]:
    raw = yaml.safe_load(DATASET.read_text(encoding="utf-8")) or {}
    return raw.get("cases", [])


def run(
    settings: Settings,
    *,
    judge: JudgeFn | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> GenerationSuiteResult:
    """Execute the suite. ``judge`` may be stubbed in tests."""
    cases = _load_dataset()
    source_scorer = SourceCountScorer()
    token_scorer = TokenLimitScorer(limit=settings.MAX_TOKENS_PER_ARTICLE)

    if judge is None:
        judge = GroundingJudge(settings).judge

    results: list[GenerationCaseResult] = []
    good_scores: list[float] = []
    bad_scores: list[float] = []

    for case in cases:
        body = case.get("body", "")
        sources = case.get("sources", [])
        label = case.get("label", "unspecified")
        sc = source_scorer.score(sources)
        tk = token_scorer.score(body)

        grounding_score: float | None = None
        if sc["source_count_passed"] and tk["body_tokens_passed"] and body.strip():
            claims = claims_from_body(body.splitlines())
            source_pairs = [(s["title"], s.get("snippet", "")) for s in sources]
            batch = judge(claims, source_pairs)
            agg = GroundingJudge.aggregate(batch, expected=len(claims))
            grounding_score = agg["eval_grounding_score"]
            if label == "known_good":
                good_scores.append(grounding_score)
            elif label == "known_bad":
                bad_scores.append(grounding_score)

        results.append(
            GenerationCaseResult(
                case_id=case["id"],
                label=label,
                source_count_passed=bool(sc["source_count_passed"]),
                body_tokens=float(tk["body_tokens"]),
                body_tokens_passed=bool(tk["body_tokens_passed"]),
                eval_grounding_score=grounding_score,
            )
        )

    # Gate metric: average grounding on known-good cases. Must be high.
    # Diagnostic: `eval_false_positive_rate` = how often a known-bad slipped
    # through as "grounded". Must be low; not a gate yet.
    gate_score = sum(good_scores) / len(good_scores) if good_scores else 1.0
    bad_mean = sum(bad_scores) / len(bad_scores) if bad_scores else 0.0
    aggregate = {
        "eval_grounding_score": gate_score,
        "eval_false_positive_rate": bad_mean,
        "cases_total": float(len(results)),
        "cases_passed_structural_gate": float(
            sum(1 for r in results if r.source_count_passed and r.body_tokens_passed)
        ),
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = now().strftime("%Y-%m-%d_%H-%M-%S")
    output = {
        "suite": "generation",
        "timestamp": timestamp,
        "model": settings.EVAL_MODEL,
        "aggregate": aggregate,
        "cases": [r.__dict__ for r in results],
    }
    out_path = RESULTS_DIR / f"{timestamp}.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return GenerationSuiteResult(cases=results, aggregate=aggregate, output_path=out_path)
