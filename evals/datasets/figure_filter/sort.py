"""Sort labeled figures into visual-review directories.

Two modes:

- ``--mode label`` (default) — copy every labeled image into
  ``useful/<doc>/`` or ``decorative/<doc>/`` based on the current
  ``label`` field in ``labels.yaml``. Safe to re-run after corrections;
  clears both directories first.

- ``--mode disputed`` — run the algorithmic filter against every label
  and dump **only the disputes** into ``review_fp/`` and ``review_fn/``.
  These are the cases where filter and ground-truth label disagree —
  the highest-signal bucket for further review or threshold tuning.
    * ``review_fp/`` contains filter **false positives** (filter
      dropped, label says useful) — these are the risk cases where the
      filter may be losing real content.
    * ``review_fn/`` contains filter **false negatives** (filter kept,
      label says decorative) — these are the wasted describer calls we
      could still save with better rules.
  Each image is prefixed with the firing rule (``<rule>__<file>``) and
  a top-level ``info.txt`` in each folder summarizes every case with
  its reasoning.

Usage::

    uv run python -m evals.datasets.figure_filter.sort
    uv run python -m evals.datasets.figure_filter.sort --mode disputed
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from app.config import env as default_env
from app.core.images.filter_images import decide
from evals.suites.figure_filter import (
    _build_hash_page_counts_per_doc,
    _entry_to_figure_bytes,
)

BASE = Path(__file__).resolve().parent
LABELS_PATH = BASE / "labels.yaml"


# ----- shared helpers ---------------------------------------------------------


def _load_labels(path: Path = LABELS_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        print(f"ERROR: {path} does not exist — run build.py first", file=sys.stderr)
        sys.exit(1)
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def _image_path(entry: dict[str, Any]) -> Path:
    return BASE / entry["image"]


def _wipe(directory: Path) -> None:
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)


# ----- mode: label ------------------------------------------------------------


def sort_by_label(entries: list[dict[str, Any]]) -> dict[str, int]:
    useful = BASE / "useful"
    decorative = BASE / "decorative"
    _wipe(useful)
    _wipe(decorative)

    stats: Counter[str] = Counter()
    for entry in entries:
        src = _image_path(entry)
        if not src.exists():
            stats["missing"] += 1
            continue
        target_root = useful if entry["label"] == "useful" else decorative
        target = target_root / entry["doc"] / src.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        stats[entry["label"]] += 1
    return dict(stats)


# ----- mode: disputed ---------------------------------------------------------


# _build_hash_page_counts_per_doc and _entry_to_figure_bytes are imported
# from the eval suite to keep the sort view exactly in sync with the scorer.


def sort_disputed(entries: list[dict[str, Any]], settings: Any = default_env) -> dict[str, int]:
    review_fp = BASE / "review_fp"
    review_fn = BASE / "review_fn"
    _wipe(review_fp)
    _wipe(review_fn)

    counts_per_doc = _build_hash_page_counts_per_doc(entries)

    stats: Counter[str] = Counter()
    fp_info: list[str] = []
    fn_info: list[str] = []

    for entry in entries:
        src = _image_path(entry)
        if not src.exists():
            stats["missing"] += 1
            continue

        fig = _entry_to_figure_bytes(entry, labels_path=LABELS_PATH)
        doc = str(entry["doc"])
        per_doc_counts = {h: n for (d, h), n in counts_per_doc.items() if d == doc}
        decision = decide(fig, per_doc_counts, settings)

        predicted = "decorative" if not decision.keep else "useful"
        ground_truth = str(entry["label"])

        if predicted == ground_truth:
            stats["agree"] += 1
            continue

        rule = decision.reason or "kept"
        prefix = f"{rule}__{src.name}"
        reasoning = str(entry.get("reasoning", "")).replace("\n", " ")[:200]

        if predicted == "decorative" and ground_truth == "useful":
            # False positive — filter dropped content the label says is useful
            target_dir = review_fp / doc
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target_dir / prefix)
            stats["false_positives"] += 1
            fp_info.append(
                f"{doc}/p{entry['page']:03d}.{entry['index']}  rule={rule}  "
                f"{entry.get('confidence', '-')}\n  {reasoning}"
            )
        else:
            # False negative — filter kept decoration
            target_dir = review_fn / doc
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target_dir / src.name)
            stats["false_negatives"] += 1
            fn_info.append(
                f"{doc}/p{entry['page']:03d}.{entry['index']}  "
                f"{entry.get('confidence', '-')}\n  {reasoning}"
            )

    # Write the info files.
    if fp_info:
        header = (
            "False positives — filter dropped these but LLM label says they're useful.\n"
            "These are RISK cases: the filter may be losing real pedagogical content.\n"
            "Filename prefix shows which rule fired (tiny / repeated / ribbon).\n\n"
        )
        (review_fp / "info.txt").write_text(header + "\n\n".join(fp_info), encoding="utf-8")
    if fn_info:
        header = (
            "False negatives — filter kept these but LLM label says they're decorative.\n"
            "These are WASTE cases: the describer is still being called on decoration.\n"
            "No rule fired, which is why they slipped through.\n\n"
        )
        (review_fn / "info.txt").write_text(header + "\n\n".join(fn_info), encoding="utf-8")

    return dict(stats)


# ----- CLI --------------------------------------------------------------------


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["label", "disputed"],
        default="label",
        help="'label' (default) sorts into useful/decorative. "
        "'disputed' sorts filter-vs-label disagreements into review_fp and review_fn.",
    )
    args = parser.parse_args()

    entries = _load_labels()
    print(f"Loaded {len(entries)} labels from {LABELS_PATH}")

    if args.mode == "label":
        stats = sort_by_label(entries)
        print("\nSorted into useful/ and decorative/:")
        print(f"  useful:     {stats.get('useful', 0)}")
        print(f"  decorative: {stats.get('decorative', 0)}")
        if stats.get("missing"):
            print(f"  missing:    {stats['missing']}")
    else:
        stats = sort_disputed(entries)
        print("\nSorted disputes into review_fp/ and review_fn/:")
        print(f"  agreements:      {stats.get('agree', 0)}")
        print(f"  false positives: {stats.get('false_positives', 0)}  (useful wrongly dropped)")
        print(f"  false negatives: {stats.get('false_negatives', 0)}  (decorative wrongly kept)")
        if stats.get("missing"):
            print(f"  missing src:     {stats['missing']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
