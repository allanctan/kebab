"""Bulk-correct LLM mislabels in ``labels.yaml``.

The first-pass labeler (``build.py``) systematically marked a class of
repeated decorative icons — lightbulbs, checklists, rulers, magnifying
glasses, hand-washing illustrations, DepEd seals, etc. — as ``useful``
even though they're generic section-header iconography with zero
pedagogical content. We know this because:

1. The algorithmic filter correctly caught them (they show up as "false
   positives" in :mod:`evals.suites.figure_filter` — filter dropped,
   label said useful).
2. The labeler's own ``reasoning`` column describes them exactly as what
   they are ("A simple lightbulb illustration, potentially representing
   an idea").

This script flips those entries deterministically based on the reasoning
column without any new LLM calls:

- Loads ``labels.yaml``
- Finds every entry where: ``label == "useful"``, the reasoning matches
  a known decorative-icon pattern, and the entry is an algorithmic-
  filter FP (filter dropped + label said useful)
- Flips ``label`` to ``"decorative"`` and marks ``reviewed: true``
- Saves ``labels.yaml`` back

Idempotent: re-running is a no-op once the flips have landed (reviewed
entries are skipped). Safe to dry-run first with ``--dry-run``.
"""

from __future__ import annotations

import argparse
import re
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


# Keyword families that are almost always decorative in educational PDFs.
# Each pattern matches reasoning text — if the labeler wrote "A simple
# lightbulb illustration", that's a lightbulb icon, not a physics
# diagram of a light source.
_DECORATIVE_PATTERNS = {
    # Generic "idea" / "task" / "tool" symbols that are never pedagogical
    "lightbulb": re.compile(r"\blight\s*bulb\b", re.IGNORECASE),
    "checklist": re.compile(r"\bchecklist\b|\bcheck(mark|box)", re.IGNORECASE),
    "magnifying_glass": re.compile(r"\bmagnifying glass\b", re.IGNORECASE),
    "pencil_icon": re.compile(
        r"\bpencil\b.*\b(writing|drawing|illustration|icon|cup)", re.IGNORECASE
    ),
    "open_book": re.compile(
        r"\bopen book\b|\bbook icon\b|\bstack(ed)? (of )?books\b|\bbooks stacked\b",
        re.IGNORECASE,
    ),
    "thought_bubble": re.compile(r"\bthought bubble\b", re.IGNORECASE),
    "question_mark": re.compile(r"\b(stylized )?question mark\b", re.IGNORECASE),
    "exclamation_mark": re.compile(r"\bexclamation mark\b", re.IGNORECASE),
    "ruler_icon": re.compile(
        r"\bruler\b.*(markings|tick|measurement|illustration|icon|represent)"
        r"|\bsimple ruler\b",
        re.IGNORECASE,
    ),
    # Hand gestures used as hygiene / process icons in DepEd modules
    "hand_symbol": re.compile(
        r"\bhand(s)?\b.*(washing|being washed|rubbing|cupped|under a|touching|holding)",
        re.IGNORECASE,
    ),
    "puzzle_piece": re.compile(r"\bpuzzle piece\b", re.IGNORECASE),
    "speech_bubble": re.compile(r"\bspeech bubble\b", re.IGNORECASE),
    # Institutional branding
    "deped_seal": re.compile(
        r"\b(DepEd|Department of Education|Schools Division|Marikina|Lungsod|divisional? seal)\b",
        re.IGNORECASE,
    ),
    # Generic person / silhouette icons
    "person_icon": re.compile(
        r"\b(illustration of a person|dynamic pose|athletic (attire|pose)|silhouette of)\b",
        re.IGNORECASE,
    ),
    # Labeler explicitly flagged as decorative/placeholder despite returning "useful"
    "explicit_decorative": re.compile(
        r"\b(likely )?decorative( element| icon)?\b"
        r"|\blikely (a )?placeholder\b"
        r"|\bornamental\b"
        r"|\bsection header\b"
        r"|\bpage header\b"
        r"|\b(serving as|used as) a (section|page) (header|divider)\b",
        re.IGNORECASE,
    ),
    # "A common symbol for X" almost always means a generic icon
    "common_symbol": re.compile(
        r"\bcommon symbol( for)?\b|\bsymbol for\b|\bsymbolizing\b|\brepresenting an idea\b",
        re.IGNORECASE,
    ),
    "stylized_simple": re.compile(
        r"\bsimple (line )?drawing of\b"
        r"|\bstylized (drawing|illustration|icon)\b"
        r"|\bsimple black and white (drawing|illustration)\b",
        re.IGNORECASE,
    ),
    "logo": re.compile(r"\b(logo|emblem|seal)\b", re.IGNORECASE),
    "rectangular_box": re.compile(r"\bopen (rectangular )?box\b", re.IGNORECASE),
    "stack_of_papers": re.compile(
        r"\bstack of (papers|documents|files)\b", re.IGNORECASE
    ),
}


def _match_decorative_patterns(reasoning: str) -> list[str]:
    """Return the list of pattern names that match the given reasoning."""
    return [name for name, rx in _DECORATIVE_PATTERNS.items() if rx.search(reasoning)]


def fix(
    labels_path: Path = LABELS_PATH,
    *,
    dry_run: bool = False,
    settings: Any = default_env,
) -> dict[str, int]:
    """Flip mislabels in place, returning a small stats dict."""
    entries = yaml.safe_load(labels_path.read_text(encoding="utf-8")) or []

    # Compute per-doc hash counts once (needed by the filter's repeat rule).
    counts = _build_hash_page_counts_per_doc(entries)

    flipped = 0
    unchanged = 0
    skipped_reviewed = 0
    flipped_examples: list[str] = []

    for entry in entries:
        if bool(entry.get("reviewed", False)):
            skipped_reviewed += 1
            continue
        if str(entry.get("label", "")) != "useful":
            continue

        # Only flip entries the filter would have dropped — these are the
        # ones where the filter's algorithmic judgement disagrees with the
        # labeler. If the filter also kept it, we don't have an automatic
        # second opinion, so we leave it alone.
        fig = _entry_to_figure_bytes(entry, labels_path=labels_path)
        doc = str(entry["doc"])
        per_doc_counts = {h: n for (d, h), n in counts.items() if d == doc}
        decision = decide(fig, per_doc_counts, settings)
        if decision.keep:
            unchanged += 1
            continue

        # The filter says drop. Does the reasoning confirm it's decorative?
        reasoning = str(entry.get("reasoning") or "")
        matches = _match_decorative_patterns(reasoning)
        if not matches:
            unchanged += 1
            continue

        if not dry_run:
            entry["label"] = "decorative"
            entry["reviewed"] = True
            entry.setdefault("correction_source", "fix_mislabels.py")
            entry["correction_patterns"] = matches
        flipped += 1
        if len(flipped_examples) < 10:
            flipped_examples.append(
                f"  {doc[:40]} p{entry['page']:03d}.{entry['index']} "
                f"[{','.join(matches)}] {reasoning[:80]}"
            )

    if not dry_run:
        labels_path.write_text(
            yaml.safe_dump(entries, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    return {
        "flipped": flipped,
        "unchanged": unchanged,
        "skipped_reviewed": skipped_reviewed,
        "examples": flipped_examples,  # type: ignore[dict-item]
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would flip without modifying labels.yaml.",
    )
    parser.add_argument(
        "--labels-path",
        type=Path,
        default=LABELS_PATH,
        help="Path to labels.yaml.",
    )
    args = parser.parse_args()

    stats = fix(args.labels_path, dry_run=args.dry_run)
    mode = "would flip" if args.dry_run else "flipped"
    print(f"{mode}: {stats['flipped']}")
    print(f"unchanged (no pattern match or filter kept): {stats['unchanged']}")
    print(f"skipped (already reviewed): {stats['skipped_reviewed']}")
    if stats["examples"]:
        print("\nSample flips:")
        for line in stats["examples"]:  # type: ignore[index]
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
