"""Verification eval suite.

Cost budget: ~$0.00 per run — :class:`InjectionDetectionJudge` is
pure-code (no LLM call). The point is to track over time how often the
verifier stage catches injected errors as the dataset grows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from app.config.config import Settings
from evals.evaluators.verification.injection_detection_judge import (
    DetectionBatch,
    InjectionDetectionJudge,
)

DATASET = Path(__file__).resolve().parent.parent / "datasets" / "verification.yaml"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results" / "verification"


@dataclass
class VerificationSuiteResult:
    batch: DetectionBatch
    aggregate: dict[str, float]
    output_path: Path


def _load_dataset() -> list[dict[str, Any]]:
    raw = yaml.safe_load(DATASET.read_text(encoding="utf-8")) or {}
    return raw.get("cases", [])


def run(
    settings: Settings,
    *,
    now: Callable[[], datetime] = datetime.now,
) -> VerificationSuiteResult:
    cases = _load_dataset()
    judge = InjectionDetectionJudge(settings)
    triples = [
        (c["article_id"], bool(c["injected"]), bool(c["verifier_passed"])) for c in cases
    ]
    batch = judge.judge(triples)
    aggregate = InjectionDetectionJudge.aggregate(batch)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = now().strftime("%Y-%m-%d_%H-%M-%S")
    output = {
        "suite": "verification",
        "timestamp": timestamp,
        "model": settings.EVAL_MODEL,
        "aggregate": aggregate,
        "verdicts": [v.model_dump() for v in batch.verdicts],
    }
    out_path = RESULTS_DIR / f"{timestamp}.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return VerificationSuiteResult(batch=batch, aggregate=aggregate, output_path=out_path)
