"""Figure-filter eval suite.

Deterministic, cheap, and free of LLM calls at run time. Reads the
committed ground-truth labels at ``evals/datasets/figure_filter/labels.yaml``
(created by ``evals/datasets/figure_filter/build.py`` and manually
reviewed), runs the current algorithmic filter against each figure, and
computes F1 metrics plus a per-rule breakdown.

Labels file shape (one entry per figure):

    - doc: <processed-doc-stem>
      page: <1-based page number>
      index: <1-based figure index within the page>
      hash: <sha256 of raw bytes>
      image: <relative path under evals/datasets/figure_filter/>
      width: <raster pixels>
      height: <raster pixels>
      rect_width: <rendered points>
      rect_height: <rendered points>
      page_width: <rendered points>
      page_height: <rendered points>
      label: decorative | useful
      reasoning: <one-sentence rationale from the labeler>
      reviewed: <true after human review; false for unreviewed LLM guess>

The scorer uses ``label`` directly — reviewers are expected to edit it
in place during manual review. The ``reviewed`` flag decides whether
an entry participates in scoring by default.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from app.config.config import Settings
from app.core.errors import KebabError
from app.core.images.filter_images import decide
from app.utils.pdf_extractor import FigureBytes, _dominant_color_usage
from evals.evaluators.figure_filter.f1_scorer import (
    F1Report,
    F1Scorer,
    LabeledFigure,
)

logger = logging.getLogger(__name__)

DATASET_DIR = Path(__file__).resolve().parent.parent / "datasets" / "figure_filter"
LABELS_PATH = DATASET_DIR / "labels.yaml"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results" / "figure_filter"


@dataclass
class FigureFilterSuiteResult:
    """Aggregate output of one suite run."""

    report: F1Report
    aggregate: dict[str, float]
    output_path: Path
    reviewed_count: int
    unreviewed_count: int
    total_labels: int


def _load_labels(path: Path = LABELS_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        raise KebabError(
            f"figure_filter: labels file not found at {path}. "
            "Run `uv run python -m evals.datasets.figure_filter.build` to create it."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise KebabError(f"figure_filter: expected a list at top level of {path}")
    return raw


def _entry_to_figure_bytes(
    entry: dict[str, Any],
    *,
    labels_path: Path = LABELS_PATH,
) -> FigureBytes:
    """Reconstruct a :class:`FigureBytes` from a labels.yaml row.

    The ``dominant_color_usage`` field was added after the initial
    labeler run, so older ``labels.yaml`` rows don't carry it. If the
    field is missing we compute it on the fly from the saved image
    file next to the labels file. This is cheap (PyMuPDF Pixmap decode)
    and runs once per eval invocation.
    """
    dominant = entry.get("dominant_color_usage")
    if dominant is None:
        image_rel = entry.get("image")
        if image_rel:
            image_path = labels_path.parent / image_rel
            if image_path.exists():
                try:
                    dominant = _dominant_color_usage(image_path.read_bytes())
                except Exception:  # noqa: BLE001
                    dominant = None

    return FigureBytes(
        page=int(entry["page"]),
        index=int(entry["index"]),
        xref=0,
        mime_type="",
        extension="",
        bytes=b"",
        width=int(entry.get("width", 0)),
        height=int(entry.get("height", 0)),
        rect_width=float(entry["rect_width"]) if entry.get("rect_width") is not None else None,
        rect_height=float(entry["rect_height"]) if entry.get("rect_height") is not None else None,
        page_width=float(entry["page_width"]) if entry.get("page_width") is not None else None,
        page_height=float(entry["page_height"]) if entry.get("page_height") is not None else None,
        content_hash=str(entry.get("hash", "")),
        dominant_color_usage=float(dominant) if dominant is not None else None,
    )


def _build_hash_page_counts_per_doc(
    entries: list[dict[str, Any]],
) -> dict[tuple[str, str], int]:
    """Count distinct pages per (doc, hash) so cross-page dedup works over labels.

    The runtime filter builds this per-doc because each PDF is processed
    independently. At eval time we see all docs in one labels file, so
    we scope counts by ``(doc, hash)`` to match.
    """
    pages_seen: dict[tuple[str, str], set[int]] = {}
    for entry in entries:
        key = (str(entry["doc"]), str(entry.get("hash", "")))
        pages_seen.setdefault(key, set()).add(int(entry["page"]))
    return {key: len(pages) for key, pages in pages_seen.items()}


def run(
    settings: Settings,
    *,
    labels_path: Path = LABELS_PATH,
    include_unreviewed: bool = False,
    now: Callable[[], datetime] = datetime.now,
) -> FigureFilterSuiteResult:
    """Execute the figure-filter eval against ``labels_path``."""
    entries = _load_labels(labels_path)
    total_labels = len(entries)

    reviewed = [e for e in entries if bool(e.get("reviewed", False))]
    unreviewed = [e for e in entries if not bool(e.get("reviewed", False))]

    scoring_entries = entries if include_unreviewed else reviewed
    if not scoring_entries:
        raise KebabError(
            f"figure_filter: no entries to score. total={total_labels}, "
            f"reviewed={len(reviewed)}. Review labels.yaml or pass "
            f"include_unreviewed=True to score against LLM labels."
        )

    # Precompute hash-page counts across the full scoring set so the
    # cross-page dedup rule sees the same distribution as it would at ingest.
    counts_per_doc = _build_hash_page_counts_per_doc(scoring_entries)

    # Run the filter on each figure's metadata and build LabeledFigure records.
    labeled: list[LabeledFigure] = []
    for entry in scoring_entries:
        fig = _entry_to_figure_bytes(entry, labels_path=labels_path)
        doc = str(entry["doc"])
        # The filter's `decide()` takes a dict keyed on bare hash; scope to this doc.
        per_doc_counts = {
            h: count
            for (d, h), count in counts_per_doc.items()
            if d == doc
        }
        decision = decide(fig, per_doc_counts, settings)
        predicted = "decorative" if not decision.keep else "useful"
        labeled.append(
            LabeledFigure(
                doc=doc,
                page=int(entry["page"]),
                index=int(entry["index"]),
                ground_truth=str(entry["label"]),  # type: ignore[arg-type]
                predicted=predicted,  # type: ignore[arg-type]
                rule=decision.reason,
            )
        )

    report = F1Scorer().score(labeled)
    aggregate = report.metrics()
    aggregate["reviewed_count"] = float(len(reviewed))
    aggregate["unreviewed_count"] = float(len(unreviewed))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = now().strftime("%Y-%m-%d_%H-%M-%S")
    output = {
        "suite": "figure_filter",
        "timestamp": timestamp,
        "labels_path": str(labels_path),
        "include_unreviewed": include_unreviewed,
        "total_labels": total_labels,
        "reviewed_count": len(reviewed),
        "unreviewed_count": len(unreviewed),
        "scored_count": len(scoring_entries),
        "aggregate": aggregate,
        "per_rule": {
            name: {
                "true_positives": cm.true_positives,
                "false_positives": cm.false_positives,
                "precision": cm.precision,
            }
            for name, cm in report.per_rule.items()
        },
        "false_positives": [
            {"doc": f.doc, "page": f.page, "index": f.index, "rule": f.rule}
            for f in report.false_positives
        ],
        "false_negatives": [
            {"doc": f.doc, "page": f.page, "index": f.index}
            for f in report.false_negatives
        ],
    }
    out_path = RESULTS_DIR / f"{timestamp}.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    logger.info(
        "figure_filter: scored %d/%d figures (reviewed=%d), F1=%.3f → %s",
        len(scoring_entries),
        total_labels,
        len(reviewed),
        report.matrix.f1,
        out_path,
    )
    return FigureFilterSuiteResult(
        report=report,
        aggregate=aggregate,
        output_path=out_path,
        reviewed_count=len(reviewed),
        unreviewed_count=len(unreviewed),
        total_labels=total_labels,
    )
