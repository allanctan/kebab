"""F1 scorer math — exhaustive truth table + per-rule attribution."""

from __future__ import annotations

import pytest

from evals.evaluators.figure_filter.f1_scorer import (
    ConfusionMatrix,
    F1Scorer,
    LabeledFigure,
)


def _fig(
    *,
    doc: str = "d",
    page: int = 1,
    index: int = 1,
    ground_truth: str = "useful",
    predicted: str = "useful",
    rule: str = "",
) -> LabeledFigure:
    return LabeledFigure(
        doc=doc,
        page=page,
        index=index,
        ground_truth=ground_truth,  # type: ignore[arg-type]
        predicted=predicted,  # type: ignore[arg-type]
        rule=rule,
    )


# ----- Confusion matrix cell classification -----------------------------------


def test_tp_when_filter_drops_decorative() -> None:
    report = F1Scorer().score([_fig(ground_truth="decorative", predicted="decorative", rule="tiny")])
    assert report.matrix.true_positives == 1
    assert report.matrix.false_positives == 0
    assert report.matrix.true_negatives == 0
    assert report.matrix.false_negatives == 0


def test_fp_when_filter_drops_useful() -> None:
    report = F1Scorer().score([_fig(ground_truth="useful", predicted="decorative", rule="repeated")])
    assert report.matrix.false_positives == 1
    assert len(report.false_positives) == 1


def test_tn_when_filter_keeps_useful() -> None:
    report = F1Scorer().score([_fig(ground_truth="useful", predicted="useful")])
    assert report.matrix.true_negatives == 1


def test_fn_when_filter_keeps_decorative() -> None:
    report = F1Scorer().score([_fig(ground_truth="decorative", predicted="useful")])
    assert report.matrix.false_negatives == 1
    assert len(report.false_negatives) == 1


# ----- Derived metrics --------------------------------------------------------


def test_perfect_classifier_yields_f1_one() -> None:
    figures = [
        _fig(ground_truth="decorative", predicted="decorative", rule="tiny"),
        _fig(ground_truth="decorative", predicted="decorative", rule="repeated"),
        _fig(ground_truth="useful", predicted="useful"),
        _fig(ground_truth="useful", predicted="useful"),
    ]
    report = F1Scorer().score(figures)
    assert report.matrix.precision == 1.0
    assert report.matrix.recall == 1.0
    assert report.matrix.f1 == 1.0
    assert report.matrix.accuracy == 1.0


def test_all_wrong_yields_f1_zero() -> None:
    figures = [
        _fig(ground_truth="decorative", predicted="useful"),
        _fig(ground_truth="useful", predicted="decorative", rule="tiny"),
    ]
    report = F1Scorer().score(figures)
    assert report.matrix.precision == 0.0
    assert report.matrix.recall == 0.0
    assert report.matrix.f1 == 0.0
    assert report.matrix.accuracy == 0.0


def test_known_confusion_matrix_derived_metrics() -> None:
    # 3 TP, 1 FP, 4 TN, 2 FN → P=0.75, R=0.6, F1≈0.667, Acc=0.7
    figures = (
        [_fig(ground_truth="decorative", predicted="decorative", rule="tiny")] * 3
        + [_fig(ground_truth="useful", predicted="decorative", rule="repeated")]
        + [_fig(ground_truth="useful", predicted="useful")] * 4
        + [_fig(ground_truth="decorative", predicted="useful")] * 2
    )
    report = F1Scorer().score(figures)
    assert report.matrix.true_positives == 3
    assert report.matrix.false_positives == 1
    assert report.matrix.true_negatives == 4
    assert report.matrix.false_negatives == 2
    assert report.matrix.precision == pytest.approx(0.75)
    assert report.matrix.recall == pytest.approx(0.6)
    assert report.matrix.f1 == pytest.approx(2 * 0.75 * 0.6 / (0.75 + 0.6))
    assert report.matrix.accuracy == pytest.approx(0.7)


def test_empty_input_yields_zero_metrics() -> None:
    report = F1Scorer().score([])
    assert report.matrix.total == 0
    assert report.matrix.precision == 0.0
    assert report.matrix.recall == 0.0
    assert report.matrix.f1 == 0.0


# ----- Per-rule attribution ---------------------------------------------------


def test_per_rule_breakdown_tracks_each_rule() -> None:
    figures = [
        _fig(ground_truth="decorative", predicted="decorative", rule="tiny"),      # tiny TP
        _fig(ground_truth="decorative", predicted="decorative", rule="tiny"),      # tiny TP
        _fig(ground_truth="useful", predicted="decorative", rule="repeated"),      # repeated FP
        _fig(ground_truth="decorative", predicted="decorative", rule="repeated"),  # repeated TP
        _fig(ground_truth="decorative", predicted="decorative", rule="ribbon"),    # ribbon TP
    ]
    report = F1Scorer().score(figures)
    assert report.per_rule["tiny"].true_positives == 2
    assert report.per_rule["tiny"].false_positives == 0
    assert report.per_rule["repeated"].true_positives == 1
    assert report.per_rule["repeated"].false_positives == 1
    assert report.per_rule["ribbon"].true_positives == 1


def test_per_rule_excludes_kept_figures() -> None:
    # Kept figures (rule="") don't contribute to per-rule counts.
    figures = [
        _fig(ground_truth="decorative", predicted="decorative", rule="tiny"),
        _fig(ground_truth="useful", predicted="useful"),  # TN, no rule
    ]
    report = F1Scorer().score(figures)
    assert "" not in report.per_rule
    assert list(report.per_rule.keys()) == ["tiny"]


# ----- metrics() dict shape ---------------------------------------------------


def test_metrics_dict_has_eval_prefix() -> None:
    figures = [_fig(ground_truth="decorative", predicted="decorative", rule="tiny")]
    metrics = F1Scorer().score(figures).metrics()
    assert "eval_f1" in metrics
    assert "eval_precision" in metrics
    assert "eval_recall" in metrics
    assert "eval_accuracy" in metrics
    assert metrics["total"] == 1.0


# ----- ConfusionMatrix properties on edge cases -------------------------------


def test_matrix_divide_by_zero_is_zero_not_error() -> None:
    m = ConfusionMatrix(true_positives=0, false_positives=0, true_negatives=0, false_negatives=0)
    assert m.precision == 0.0
    assert m.recall == 0.0
    assert m.f1 == 0.0
    assert m.accuracy == 0.0
