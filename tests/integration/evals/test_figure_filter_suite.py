"""figure_filter suite — end-to-end scoring against a synthetic labels file."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from app.config.config import Settings
from app.core.errors import KebabError
from evals.suites import figure_filter


def _now() -> datetime:
    return datetime(2026, 4, 9, 15, 0, 0)


@pytest.fixture
def settings() -> Settings:
    return Settings(GOOGLE_API_KEY="test-key")


def _write_labels(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")


def _entry(
    *,
    doc: str = "DocA",
    page: int = 1,
    index: int = 1,
    label: str = "useful",
    reviewed: bool = True,
    rect_w: float = 200.0,
    rect_h: float = 150.0,
    page_w: float = 612.0,
    page_h: float = 792.0,
    hash_: str = "unique",
) -> dict:
    return {
        "doc": doc,
        "page": page,
        "index": index,
        "hash": hash_,
        "image": f"images/{doc}/p{page:03d}_f{index:02d}.png",
        "width": 400,
        "height": 300,
        "rect_width": rect_w,
        "rect_height": rect_h,
        "page_width": page_w,
        "page_height": page_h,
        "label": label,
        "reasoning": "fixture",
        "reviewed": reviewed,
    }


@pytest.mark.integration
def test_suite_scores_perfect_filter(
    tmp_path: Path, settings: Settings
) -> None:
    """When the filter's decisions match every ground-truth label, F1 = 1.0."""
    labels = tmp_path / "labels.yaml"
    _write_labels(
        labels,
        [
            # Repeated seal on 3 pages → filter correctly drops as "repeated", label "decorative"
            _entry(page=1, index=1, label="decorative", hash_="seal"),
            _entry(page=2, index=1, label="decorative", hash_="seal"),
            _entry(page=3, index=1, label="decorative", hash_="seal"),
            # Unique content → filter keeps, label "useful"
            _entry(page=1, index=2, label="useful", hash_="content-A"),
            _entry(page=2, index=2, label="useful", hash_="content-B"),
        ],
    )
    result = figure_filter.run(settings, labels_path=labels, now=_now)
    assert result.aggregate["eval_f1"] == 1.0
    assert result.aggregate["eval_precision"] == 1.0
    assert result.aggregate["eval_recall"] == 1.0
    assert result.aggregate["eval_accuracy"] == 1.0
    assert result.report.false_positives == []
    assert result.report.false_negatives == []
    assert result.output_path.exists()


@pytest.mark.integration
def test_suite_tracks_filter_mistakes(
    tmp_path: Path, settings: Settings
) -> None:
    """A filter false-positive (drops useful) and false-negative (keeps decorative) both show up."""
    labels = tmp_path / "labels.yaml"
    _write_labels(
        labels,
        [
            # Tiny but labeled useful → filter drops (FP for positive class "decorative")
            _entry(page=1, index=1, label="useful", rect_w=20, rect_h=20, hash_="tiny-science"),
            # Unique, normal-size, labeled decorative → filter keeps (FN)
            _entry(page=5, index=1, label="decorative", hash_="unique-seal"),
        ],
    )
    result = figure_filter.run(settings, labels_path=labels, now=_now)
    assert result.report.matrix.false_positives == 1
    assert result.report.matrix.false_negatives == 1
    assert len(result.report.false_positives) == 1
    assert result.report.false_positives[0].page == 1
    assert result.report.false_positives[0].rule == "tiny"
    assert len(result.report.false_negatives) == 1
    assert result.report.false_negatives[0].page == 5


@pytest.mark.integration
def test_suite_skips_unreviewed_by_default(
    tmp_path: Path, settings: Settings
) -> None:
    labels = tmp_path / "labels.yaml"
    _write_labels(
        labels,
        [
            _entry(page=1, index=1, label="useful", reviewed=True),
            _entry(page=2, index=1, label="decorative", reviewed=False),
            _entry(page=3, index=1, label="useful", reviewed=False),
        ],
    )
    result = figure_filter.run(settings, labels_path=labels, now=_now)
    # Only the single reviewed entry is scored.
    assert int(result.aggregate["total"]) == 1
    assert result.reviewed_count == 1
    assert result.unreviewed_count == 2


@pytest.mark.integration
def test_suite_include_unreviewed_scores_all(
    tmp_path: Path, settings: Settings
) -> None:
    labels = tmp_path / "labels.yaml"
    _write_labels(
        labels,
        [
            _entry(page=1, index=1, label="useful", reviewed=False),
            _entry(page=2, index=1, label="decorative", reviewed=False, hash_="sealA"),
            _entry(page=3, index=1, label="decorative", reviewed=False, hash_="sealA"),
            _entry(page=4, index=1, label="decorative", reviewed=False, hash_="sealA"),
        ],
    )
    result = figure_filter.run(
        settings, labels_path=labels, include_unreviewed=True, now=_now
    )
    assert int(result.aggregate["total"]) == 4


@pytest.mark.integration
def test_suite_errors_when_no_labels_file(
    tmp_path: Path, settings: Settings
) -> None:
    with pytest.raises(KebabError, match="labels file not found"):
        figure_filter.run(settings, labels_path=tmp_path / "missing.yaml")


@pytest.mark.integration
def test_suite_errors_when_nothing_to_score(
    tmp_path: Path, settings: Settings
) -> None:
    labels = tmp_path / "labels.yaml"
    _write_labels(labels, [_entry(reviewed=False)])
    with pytest.raises(KebabError, match="no entries to score"):
        figure_filter.run(settings, labels_path=labels)


@pytest.mark.integration
def test_suite_cross_doc_hash_dedup_is_per_doc(
    tmp_path: Path, settings: Settings
) -> None:
    """The hash-repetition rule must scope counts by document, not globally.

    A seal in DocA appearing on 3 pages AND a seal in DocB with the same
    hash on 2 pages should only trigger the 'repeated' rule in DocA.
    """
    labels = tmp_path / "labels.yaml"
    _write_labels(
        labels,
        [
            # DocA: hash appears on 3 pages → filter drops all 3
            _entry(doc="DocA", page=1, index=1, label="decorative", hash_="shared"),
            _entry(doc="DocA", page=2, index=1, label="decorative", hash_="shared"),
            _entry(doc="DocA", page=3, index=1, label="decorative", hash_="shared"),
            # DocB: same hash, only 2 pages → filter keeps both
            _entry(doc="DocB", page=1, index=1, label="useful", hash_="shared"),
            _entry(doc="DocB", page=2, index=1, label="useful", hash_="shared"),
        ],
    )
    result = figure_filter.run(settings, labels_path=labels, now=_now)
    # All 5 should be correct: 3 TPs from DocA, 2 TNs from DocB.
    assert result.report.matrix.true_positives == 3
    assert result.report.matrix.true_negatives == 2
    assert result.report.matrix.false_positives == 0
    assert result.report.matrix.false_negatives == 0
    assert result.aggregate["eval_f1"] == 1.0
