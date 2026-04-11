"""Q&A eval suite.

Cost budget: ~$0.04 per run with gemini-flash (two judges per pair).

Runs ``atomic_scorer`` first as a structural gate. Calls
``GroundedJudge`` (gate) and ``UsefulnessJudge`` (diagnostic) only on
pairs that pass the structural gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from app.config.config import Settings
from evals.evaluators.qa.atomic_scorer import AtomicScorer
from evals.evaluators.qa.grounded_judge import GroundedJudge, QaGroundedBatch
from evals.evaluators.qa.usefulness_judge import QaUsefulnessBatch, UsefulnessJudge

DATASET = Path(__file__).resolve().parent.parent / "datasets" / "qa.yaml"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results" / "qa"


@dataclass
class QaCaseResult:
    case_id: str
    label: str
    qa_atomic_passed: bool
    is_grounded: bool | None
    usefulness: int | None


@dataclass
class QaSuiteResult:
    cases: list[QaCaseResult]
    aggregate: dict[str, float]
    output_path: Path


GroundedFn = Callable[[list[tuple[str, str, list[str]]]], QaGroundedBatch]
UsefulnessFn = Callable[[list[tuple[str, str]]], QaUsefulnessBatch]


def _load_dataset() -> list[dict[str, Any]]:
    raw = yaml.safe_load(DATASET.read_text(encoding="utf-8")) or {}
    return raw.get("cases", [])


def run(
    settings: Settings,
    *,
    grounded_fn: GroundedFn | None = None,
    usefulness_fn: UsefulnessFn | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> QaSuiteResult:
    cases = _load_dataset()
    atomic = AtomicScorer()

    if grounded_fn is None:
        grounded_fn = GroundedJudge(settings).judge
    if usefulness_fn is None:
        usefulness_fn = UsefulnessJudge(settings).judge

    structural_passes: list[int] = []
    grounded_inputs: list[tuple[str, str, list[str]]] = []
    useful_inputs: list[tuple[str, str]] = []
    results: list[QaCaseResult] = []

    for idx, case in enumerate(cases):
        atomic_score = atomic.score(case["question"])
        passed = bool(atomic_score["qa_atomic_passed"])
        results.append(
            QaCaseResult(
                case_id=case["id"],
                label=case.get("label", "unspecified"),
                qa_atomic_passed=passed,
                is_grounded=None,
                usefulness=None,
            )
        )
        if passed:
            structural_passes.append(idx)
            grounded_inputs.append(
                (case["question"], case["answer"], case.get("sources", []))
            )
            useful_inputs.append((case["question"], case["answer"]))

    grounded_batch = grounded_fn(grounded_inputs) if grounded_inputs else QaGroundedBatch(verdicts=[])
    useful_batch = usefulness_fn(useful_inputs) if useful_inputs else QaUsefulnessBatch(verdicts=[])

    for verdict, idx in zip(grounded_batch.verdicts, structural_passes, strict=True):
        results[idx].is_grounded = verdict.is_grounded
    for verdict, idx in zip(useful_batch.verdicts, structural_passes, strict=True):
        results[idx].usefulness = verdict.score

    # Gate metrics over known_good cases only. Adversarial cases produce
    # `eval_false_positive_rate` (known_bad that slipped through as grounded).
    good_grounded = [
        int(r.is_grounded)
        for r in results
        if r.label == "known_good" and r.is_grounded is not None
    ]
    good_useful = [
        r.usefulness
        for r in results
        if r.label == "known_good" and r.usefulness is not None
    ]
    bad_grounded = [
        int(r.is_grounded)
        for r in results
        if r.label == "known_bad" and r.is_grounded is not None
    ]
    aggregate = {
        "eval_grounded_score": (
            sum(good_grounded) / len(good_grounded) if good_grounded else 1.0
        ),
        "eval_usefulness_score": (
            sum(good_useful) / len(good_useful) if good_useful else 0.0
        ),
        "eval_false_positive_rate": (
            sum(bad_grounded) / len(bad_grounded) if bad_grounded else 0.0
        ),
        "cases_total": float(len(results)),
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = now().strftime("%Y-%m-%d_%H-%M-%S")
    output = {
        "suite": "qa",
        "timestamp": timestamp,
        "model": settings.EVAL_MODEL,
        "aggregate": aggregate,
        "cases": [r.__dict__ for r in results],
    }
    out_path = RESULTS_DIR / f"{timestamp}.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return QaSuiteResult(cases=results, aggregate=aggregate, output_path=out_path)
