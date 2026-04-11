"""Deterministic confusion matrix + F1 scorer for the figure filter.

Positive class is "decorative" — the thing the filter drops. Given ground
truth labels and the filter's predictions on the same set of figures,
computes the standard classification metrics plus a per-rule attribution
of true/false positives so operators can see which rule is earning its
keep.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Literal

Label = Literal["decorative", "useful"]


@dataclass
class LabeledFigure:
    """One ground-truth labeled figure plus the filter's prediction.

    ``ground_truth`` is the reviewed label (human override preferred over
    LLM label — the caller is responsible for merging before passing).
    ``predicted`` is the filter's decision: ``"decorative"`` if any rule
    fired, ``"useful"`` otherwise.
    ``rule`` is the name of the rule that fired (``"tiny"``, ``"repeated"``,
    ``"ribbon"``) or empty when the filter kept the figure.
    """

    doc: str
    page: int
    index: int
    ground_truth: Label
    predicted: Label
    rule: str = ""


@dataclass
class ConfusionMatrix:
    """Standard binary confusion matrix, positive class = 'decorative'."""

    true_positives: int = 0
    false_positives: int = 0
    true_negatives: int = 0
    false_negatives: int = 0

    @property
    def total(self) -> int:
        return (
            self.true_positives
            + self.false_positives
            + self.true_negatives
            + self.false_negatives
        )

    @property
    def accuracy(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.true_positives + self.true_negatives) / self.total

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return (self.true_positives / denom) if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return (self.true_positives / denom) if denom else 0.0

    @property
    def f1(self) -> float:
        p = self.precision
        r = self.recall
        return (2 * p * r / (p + r)) if (p + r) else 0.0


@dataclass
class F1Report:
    """Full scoring output for a figure-filter run."""

    matrix: ConfusionMatrix
    per_rule: dict[str, ConfusionMatrix] = field(default_factory=dict)
    false_positives: list[LabeledFigure] = field(default_factory=list)
    false_negatives: list[LabeledFigure] = field(default_factory=list)

    def metrics(self) -> dict[str, float]:
        return {
            "eval_accuracy": self.matrix.accuracy,
            "eval_precision": self.matrix.precision,
            "eval_recall": self.matrix.recall,
            "eval_f1": self.matrix.f1,
            "true_positives": float(self.matrix.true_positives),
            "false_positives": float(self.matrix.false_positives),
            "true_negatives": float(self.matrix.true_negatives),
            "false_negatives": float(self.matrix.false_negatives),
            "total": float(self.matrix.total),
        }


@dataclass
class F1Scorer:
    """Compute confusion matrix + F1 over a set of labeled figures."""

    def score(self, figures: Iterable[LabeledFigure]) -> F1Report:
        matrix = ConfusionMatrix()
        per_rule: dict[str, ConfusionMatrix] = defaultdict(ConfusionMatrix)
        false_positives: list[LabeledFigure] = []
        false_negatives: list[LabeledFigure] = []

        for fig in figures:
            cell = _classify(fig.ground_truth, fig.predicted)
            _add(matrix, cell)
            if fig.rule:
                _add(per_rule[fig.rule], cell)
            if cell == "fp":
                false_positives.append(fig)
            elif cell == "fn":
                false_negatives.append(fig)

        return F1Report(
            matrix=matrix,
            per_rule=dict(per_rule),
            false_positives=false_positives,
            false_negatives=false_negatives,
        )


def _classify(ground_truth: Label, predicted: Label) -> Literal["tp", "fp", "tn", "fn"]:
    """Classify one figure into a confusion-matrix cell.

    Positive class is 'decorative'.
    """
    if predicted == "decorative" and ground_truth == "decorative":
        return "tp"
    if predicted == "decorative" and ground_truth == "useful":
        return "fp"
    if predicted == "useful" and ground_truth == "useful":
        return "tn"
    return "fn"


def _add(matrix: ConfusionMatrix, cell: Literal["tp", "fp", "tn", "fn"]) -> None:
    if cell == "tp":
        matrix.true_positives += 1
    elif cell == "fp":
        matrix.false_positives += 1
    elif cell == "tn":
        matrix.true_negatives += 1
    else:
        matrix.false_negatives += 1
