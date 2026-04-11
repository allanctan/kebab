"""Run the algorithmic filter against every figure in every raw PDF and
sort images by the filter's decision for visual review.

Walks ``settings.RAW_DIR/documents/**/*.pdf``, extracts every figure
(bypassing labels.yaml entirely), applies the figure filter pipeline,
and copies each image into either ``useful/<doc>/`` (filter kept) or
``decorative/<doc>/`` (filter dropped). Dropped images are prefixed
with the firing rule name (e.g. ``tiny__p001_f01.png``) so operators
can see at a glance which rule fired.

Output layout::

    evals/datasets/figure_filter/
    ├── useful/
    │   └── <doc>/pNNN_fMM.ext
    └── decorative/
        └── <doc>/<rule>__pNNN_fMM.ext

This is purely a visual-review aid — it produces no labels, no
classifications, no LLM calls. Re-running wipes and regenerates the
two directories.

Usage::

    uv run python -m evals.datasets.figure_filter.filter_all
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.config import env as default_env
from app.config.config import Settings
from app.core.images.filters import decide
from app.utils.pdf_extractor import extract


@dataclass
class FilterAllResult:
    """Summary of a filter-all run."""

    stats: Counter[str] = field(default_factory=Counter)
    per_doc: dict[str, Counter[str]] = field(default_factory=dict)

BASE = Path(__file__).resolve().parent
USEFUL = BASE / "useful"
DECORATIVE = BASE / "decorative"


def _slug(stem: str) -> str:
    return stem.replace("/", "-").replace(" ", "_")


def _wipe(directory: Path) -> None:
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)


def _iter_pdfs(raw_dir: Path):
    for pdf in sorted(raw_dir.rglob("*.pdf")):
        if pdf.parent.resolve() == raw_dir.resolve():
            continue
        yield pdf


def filter_all(settings: Settings) -> FilterAllResult:
    raw_dir = Path(settings.RAW_DIR) / "documents"
    if not raw_dir.exists():
        print(f"ERROR: {raw_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    _wipe(USEFUL)
    _wipe(DECORATIVE)

    result = FilterAllResult()
    stats = result.stats
    per_doc = result.per_doc

    for pdf in _iter_pdfs(raw_dir):
        stem = _slug(pdf.stem)
        extraction = extract(pdf, extract_figures=True)
        # Build per-doc hash-page counts (same scoping as ingest).
        hash_pages: dict[str, set[int]] = {}
        for fig in extraction.figures:
            if fig.content_hash:
                hash_pages.setdefault(fig.content_hash, set()).add(fig.page)
        counts = {h: len(pages) for h, pages in hash_pages.items()}

        doc_stats: Counter[str] = Counter()
        for fig in extraction.figures:
            decision = decide(fig, counts, settings)
            filename = f"p{fig.page:03d}_f{fig.index:02d}.{fig.extension}"
            if decision.keep:
                target_dir = USEFUL / stem
                target_name = filename
                doc_stats["useful"] += 1
                stats["useful"] += 1
            else:
                target_dir = DECORATIVE / stem
                target_name = f"{decision.reason}__{filename}"
                doc_stats[f"decorative_{decision.reason}"] += 1
                stats[f"decorative_{decision.reason}"] += 1
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / target_name).write_bytes(fig.bytes)
            stats["total"] += 1
        per_doc[stem] = doc_stats

    return result


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()  # no args; the parser exists for --help

    result = filter_all(default_env)
    stats = result.stats
    per_doc = result.per_doc

    total = stats["total"]
    useful = stats["useful"]
    decorative_total = total - useful
    print(
        f"\nProcessed {total} figures across {len(per_doc)} documents:"
    )
    print(f"  useful (kept):     {useful}  ({useful / total * 100:.1f}%)")
    print(f"  decorative (drop): {decorative_total}  ({decorative_total / total * 100:.1f}%)")
    print()
    print("  By dropping rule:")
    for key, count in sorted(stats.items()):
        if key.startswith("decorative_"):
            rule = key.removeprefix("decorative_")
            print(f"    {rule:<12s}  {count}")

    print("\nPer-doc summary (kept / dropped):")
    for doc, doc_stats in sorted(per_doc.items()):
        kept = doc_stats.get("useful", 0)
        dropped = sum(v for k, v in doc_stats.items() if k != "useful")
        total_doc = kept + dropped
        print(f"  {doc[:55]:<55s}  {kept:>4} / {dropped:>4}  ({total_doc} total)")

    print("\nBrowse: evals/datasets/figure_filter/useful/ and decorative/")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
