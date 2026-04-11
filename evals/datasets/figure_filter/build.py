"""Build the figure_filter ground-truth dataset.

One-time / rarely-run script that:

1. Walks every PDF under ``settings.RAW_DIR/documents/``
2. Extracts every figure with its rendered rect, dimensions, and SHA256
3. Calls a **distinct** Gemini labeler (``PedagogicalJudge``) on each
   image — not the describer prompt — to get a structured verdict:
   ``{label, reasoning, confidence}``
4. Writes the image bytes to ``evals/datasets/figure_filter/images/<doc>/``
5. Appends/updates an entry in ``evals/datasets/figure_filter/labels.yaml``
   with the labeler's first-pass verdict and ``reviewed: false``

Idempotent: re-runs skip figures already present in ``labels.yaml``
(matched on ``(doc, page, index, hash)``), so you can add new PDFs and
re-run without paying for the old ones again.

Usage::

    uv run python -m evals.datasets.figure_filter.build

After running, **manually review** ``labels.yaml`` — edit ``label`` in
place where the LLM got it wrong, flip ``reviewed: true`` on every entry
you've inspected. Then run ``kebab eval figure-filter`` to score the
algorithmic filter against the reviewed ground truth.

Cost budget
-----------
One Gemini call per figure at ``gemini-2.5-flash-lite`` pricing.
For a 1200-figure corpus that's ~$0.06 total. Runs in ~20 minutes
serially.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.config.config import Settings
from app.config import env as default_env
from app.config.logging import setup_logging
from app.core.errors import ConfigError, KebabError
from app.core.llm.resolve import resolve_model
from app.utils.pdf_extractor import extract

logger = logging.getLogger(__name__)

DATASET_DIR = Path(__file__).resolve().parent
LABELS_PATH = DATASET_DIR / "labels.yaml"
IMAGES_DIR = DATASET_DIR / "images"


# --- labeler agent ------------------------------------------------------------


class PedagogicalVerdict(BaseModel):
    """Structured verdict from the labeler for a single image."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str = Field(..., description="One sentence analyzing what's visible.")
    label: str = Field(..., description="Either 'decorative' or 'useful'.")
    confidence: str = Field(..., description="'low', 'medium', or 'high'.")


_SYSTEM_PROMPT = """You classify images extracted from educational PDFs into pedagogical vs. decorative content.

A knowledge base describer will later write a caption for each pedagogical image.
Your job is to decide whether the image is worth captioning at all.

## Classification rules

USEFUL (label="useful"):
- Diagrams with labeled parts, arrows, axes
- Charts, graphs, tables rendered as images
- Photographs of people, objects, phenomena, or places that illustrate a concept
- Scientific process illustrations (cell diagrams, circuit schematics, force vectors)
- Maps showing geographic or geologic data
- Equations or mathematical notation rendered as images

DECORATIVE (label="decorative"):
- Institutional logos, school seals, government emblems — ALWAYS decorative even
  if they contain text like "Department of Education"
- Page headers, page footers, watermarks
- Decorative borders, separator bars, ornamental icons
- Section dividers, bullet markers, thin ribbons
- Uniform color blocks, blank-or-near-blank images, page numbers
- Generic ornamental illustrations (e.g. stylized stars, waves, dots)

## Output format (PedagogicalVerdict)

- `reasoning`: one sentence (max ~20 words) describing what's visible AND why
  the verdict is decorative or useful.
- `label`: exactly `"useful"` or `"decorative"` (lowercase).
- `confidence`: `"low"`, `"medium"`, or `"high"`.

## Examples

Image: a school district logo with text "Marikina City Schools"
→ {reasoning: "Institutional school seal, not pedagogical content.", label: "decorative", confidence: "high"}

Image: a diagram showing tectonic plate boundaries with labeled arrows
→ {reasoning: "Labeled tectonic plate diagram showing movement directions.", label: "useful", confidence: "high"}

Image: a thin black horizontal line across the page width
→ {reasoning: "Thin horizontal separator ribbon with no content.", label: "decorative", confidence: "high"}

If uncertain, prefer "useful" with confidence "low" — the filter's goal is to
avoid accidentally dropping real content.
"""


@dataclass
class PedagogicalJudge:
    """LLM labeler for figure usefulness. Built per call to avoid event-loop issues."""

    settings: Settings

    def classify(self, image_bytes: bytes, mime_type: str) -> PedagogicalVerdict:
        """Return the labeler's verdict for one image."""
        if not self.settings.GOOGLE_API_KEY:
            raise ConfigError("KEBAB_GOOGLE_API_KEY is empty — required for labeling")

        # We build a fresh pydantic-ai Agent per call (per plan §M15) to
        # keep state off module scope and let structured-output retries
        # work cleanly under sync.
        agent = Agent(
            model=resolve_model(self.settings.FAST_MODEL),
            output_type=PedagogicalVerdict,
            system_prompt=_SYSTEM_PROMPT,
            retries=self.settings.LLM_MAX_RETRIES,
        )
        # pydantic-ai takes multi-part prompts via BinaryContent.
        from pydantic_ai import BinaryContent

        result = agent.run_sync(
            [
                "Classify this image:",
                BinaryContent(data=image_bytes, media_type=mime_type),
            ]
        )
        verdict = result.output
        # Normalize labels to the canonical lowercase form.
        label = verdict.label.strip().lower()
        if label not in {"decorative", "useful"}:
            logger.warning(
                "labeler returned unexpected label %r — defaulting to 'useful' (keeps safe)",
                verdict.label,
            )
            label = "useful"
        confidence = verdict.confidence.strip().lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "low"
        return PedagogicalVerdict(
            reasoning=verdict.reasoning,
            label=label,
            confidence=confidence,
        )


# --- dataset build ------------------------------------------------------------


def _load_existing_labels(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise KebabError(f"labels file malformed (expected list): {path}")
    return raw


def _entry_key(entry: dict[str, Any]) -> tuple[str, int, int, str]:
    return (
        str(entry["doc"]),
        int(entry["page"]),
        int(entry["index"]),
        str(entry.get("hash", "")),
    )


def _slug(stem: str) -> str:
    return stem.replace("/", "-").replace(" ", "_")


def _save_labels(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Stable ordering by (doc, page, index) so diffs are clean.
    entries.sort(key=lambda e: (str(e["doc"]), int(e["page"]), int(e["index"])))
    path.write_text(
        yaml.safe_dump(entries, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _iter_pdfs(raw_dir: Path):
    for pdf in sorted(raw_dir.rglob("*.pdf")):
        if pdf.parent.resolve() == raw_dir.resolve():
            # Skip flat-level copies produced by ingest itself.
            continue
        yield pdf


def build(
    settings: Settings,
    *,
    labels_path: Path = LABELS_PATH,
    images_dir: Path = IMAGES_DIR,
    dry_run: bool = False,
    docs: list[str] | None = None,
) -> dict[str, int]:
    """Walk raw PDFs, label every figure, append to ``labels.yaml``.

    If ``docs`` is provided, only PDFs whose slugified stem matches one
    of the listed values are processed. Useful for incrementally
    extending the dataset one document at a time.

    Returns a small stats dict for the caller/CLI to print.
    """
    raw_dir = Path(settings.RAW_DIR) / "documents"
    if not raw_dir.exists():
        raise KebabError(f"raw dir not found: {raw_dir}")

    existing = _load_existing_labels(labels_path)
    seen = {_entry_key(e) for e in existing}

    judge = PedagogicalJudge(settings=settings)
    stats = {"pdfs": 0, "figures_total": 0, "skipped_existing": 0, "labeled_new": 0, "errors": 0}

    allowed: set[str] | None = set(docs) if docs else None

    for pdf in _iter_pdfs(raw_dir):
        stem = _slug(pdf.stem)
        if allowed is not None and stem not in allowed:
            continue
        stats["pdfs"] += 1
        doc_img_dir = images_dir / stem
        extraction = extract(pdf, extract_figures=True)
        for fig in extraction.figures:
            stats["figures_total"] += 1
            key = (stem, fig.page, fig.index, fig.content_hash)
            if key in seen:
                stats["skipped_existing"] += 1
                continue
            rel_img_path = f"images/{stem}/p{fig.page:03d}_f{fig.index:02d}.{fig.extension}"

            if dry_run:
                logger.info("[dry-run] would label %s", rel_img_path)
                continue

            # Persist the image bytes first so even a failed label leaves the image for review.
            doc_img_dir.mkdir(parents=True, exist_ok=True)
            (images_dir / stem / f"p{fig.page:03d}_f{fig.index:02d}.{fig.extension}").write_bytes(
                fig.bytes
            )

            try:
                verdict = judge.classify(fig.bytes, fig.mime_type)
            except Exception as exc:  # noqa: BLE001
                logger.warning("label failed for %s p%d.%d: %s", stem, fig.page, fig.index, exc)
                stats["errors"] += 1
                verdict = PedagogicalVerdict(
                    reasoning=f"labeler error: {exc}",
                    label="useful",  # fail safe — keep by default
                    confidence="low",
                )

            entry = {
                "doc": stem,
                "page": fig.page,
                "index": fig.index,
                "hash": fig.content_hash,
                "image": rel_img_path,
                "width": fig.width,
                "height": fig.height,
                "rect_width": fig.rect_width,
                "rect_height": fig.rect_height,
                "page_width": fig.page_width,
                "page_height": fig.page_height,
                "label": verdict.label,
                "reasoning": verdict.reasoning,
                "confidence": verdict.confidence,
                "reviewed": False,
            }
            existing.append(entry)
            seen.add(key)
            stats["labeled_new"] += 1
            logger.info(
                "labeled %s p%d.%d → %s (%s)",
                stem,
                fig.page,
                fig.index,
                verdict.label,
                verdict.confidence,
            )

            # Save incrementally every 25 new labels so an interrupt doesn't lose work.
            if stats["labeled_new"] % 25 == 0:
                _save_labels(labels_path, existing)

    _save_labels(labels_path, existing)
    logger.info(
        "build complete: %d PDFs, %d figures, %d skipped (cached), %d newly labeled, %d errors",
        stats["pdfs"],
        stats["figures_total"],
        stats["skipped_existing"],
        stats["labeled_new"],
        stats["errors"],
    )
    return stats


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Walk the PDFs and list what would be labeled without calling Gemini.",
    )
    parser.add_argument(
        "--labels-path",
        type=Path,
        default=LABELS_PATH,
        help="Path to the labels YAML file (default: evals/datasets/figure_filter/labels.yaml).",
    )
    parser.add_argument(
        "--doc",
        action="append",
        default=None,
        help="Only label the given slugified doc stem (e.g. "
        "'SCI10_Q1_M2_Plate_Boundaries'). Repeatable. Useful for "
        "incrementally extending the dataset one source at a time.",
    )
    args = parser.parse_args()

    setup_logging()
    try:
        stats = build(
            default_env,
            labels_path=args.labels_path,
            dry_run=args.dry_run,
            docs=args.doc,
        )
    except KebabError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"PDFs: {stats['pdfs']}, figures: {stats['figures_total']}, "
        f"skipped (cached): {stats['skipped_existing']}, "
        f"newly labeled: {stats['labeled_new']}, errors: {stats['errors']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
