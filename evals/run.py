"""Eval CLI entrypoint.

Used by ``kebab eval <suite>``. Returns the suite result so the CLI
can compare against the committed baseline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.config import Settings
from evals.suites import generation, qa, verification

SUITES = {
    "generation": generation.run,
    "verification": verification.run,
    "qa": qa.run,
}

BASELINE_DIR = Path(__file__).resolve().parent / "suites"


@dataclass
class BaselineCheck:
    """Outcome of comparing aggregate metrics against the committed baseline."""

    suite: str
    passed: bool
    failures: list[tuple[str, float, float]]  # (metric, observed, floor)


def load_baseline(suite: str) -> dict[str, Any]:
    path = BASELINE_DIR / f"{suite}_baseline.json"
    return json.loads(path.read_text(encoding="utf-8"))


def compare_to_baseline(suite: str, aggregate: dict[str, float]) -> BaselineCheck:
    baseline = load_baseline(suite)
    floors = baseline.get("metric_floors", {})
    failures: list[tuple[str, float, float]] = []
    for metric, floor in floors.items():
        observed = aggregate.get(metric)
        if observed is None or observed < float(floor):
            failures.append((metric, float(observed or 0.0), float(floor)))
    return BaselineCheck(suite=suite, passed=not failures, failures=failures)


def run_suite(suite: str, settings: Settings) -> Any:
    if suite not in SUITES:
        raise KeyError(f"unknown suite: {suite}; known: {sorted(SUITES)}")
    return SUITES[suite](settings)
