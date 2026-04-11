"""Ingest PDFs: copy to ``raw/`` and synthesize derivatives into ``processed/``.

Spec §6 (Stage 0):
- Keep the binary untouched in ``raw/documents/<basename>.pdf``.
- Write all derived artifacts under
  ``processed/documents/<stem>/``:
    * ``text.md``     — per-page text with inline ``[Figure p.N: …]`` markers
    * ``figures.json`` — metadata + descriptions for every extracted figure
    * ``figures/``    — raw PNG/JPG bytes, one file per figure

Idempotency: if ``text.md`` already exists for a stem, the PDF is NOT
re-processed and figures are NOT re-described. Pass ``force=True`` to
override.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.config.config import Settings
from app.core.errors import IngestError
from app.core.images.filters import build_hash_page_counts, decide
from app.core.llm.multimodal import describe_image
from app.utils.pdf_extractor import FigureBytes, PdfExtraction, extract

logger = logging.getLogger(__name__)


#: Callable: (image_bytes, mime_type, settings, width, height, context) -> caption.
Describer = Callable[..., str]


@dataclass
class FigureRecord:
    """Serialized record of every extracted figure — described or filtered.

    Persisted to ``processed/documents/<stem>/figures.json`` as the audit
    trail so operators can later answer "why was this dropped?" without
    re-opening the source PDF. Downstream tools (eval scorer, sort, etc.)
    also read these fields.
    """

    page: int
    index: int
    path: str
    mime_type: str
    width: int
    height: int
    description: str
    # Filter rule that dropped this figure: "tiny", "solid_color",
    # "repeated", or "ribbon". Empty string when the figure passed all
    # rules and was sent to the describer.
    skip_reason: str = ""
    # Rendered rect on the page in PDF points (not pixels) — what the
    # filter uses to decide tiny / ribbon. Stored so eval tooling can
    # audit decisions without re-extracting from the PDF.
    rect_width: float | None = None
    rect_height: float | None = None
    page_width: float | None = None
    page_height: float | None = None
    rel_area: float | None = None
    aspect: float | None = None
    #: Fraction of pixels that are the single most-common color (0–1).
    #: Drives the solid_color rule.
    dominant_color_usage: float | None = None
    #: SHA256 of the raw image bytes, used by the repeated-hash rule.
    content_hash: str = ""


@dataclass
class PdfIngestResult:
    """Output paths produced by :func:`ingest`."""

    original: Path
    processed_dir: Path
    text_path: Path
    figures_path: Path
    chars: int
    figure_count: int
    described_count: int
    #: Count of figures where the describer raised an error (after retries)
    #: and the record was stamped ``skip_reason="describer_error"``. These
    #: are recoverable via ``kebab ingest retry-errors``.
    labeler_errors: int = 0
    skipped: bool = False
    figures: list[FigureRecord] = field(default_factory=list)


def _slug(stem: str) -> str:
    """Filesystem-safe slug for ``processed/documents/<stem>/``."""
    return stem.replace("/", "-").replace(" ", "_")


def _sha256(path: Path) -> str:
    """Return hex SHA256 digest of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _figure_marker(fig: FigureBytes, description: str) -> str:
    return f"[Figure p{fig.page}.{fig.index}: {description}]"


def _render_markdown(
    extraction: PdfExtraction, descriptions: dict[tuple[int, int], str]
) -> str:
    """Stitch per-page text with inline figure markers. Skips DECORATIVE figures."""
    chunks: list[str] = []
    for page in extraction.pages:
        chunks.append(f"## Page {page.page_number}\n")
        if page.text.strip():
            chunks.append(page.text.strip())
            chunks.append("")
        for fig in page.figures:
            desc = descriptions.get((fig.page, fig.index), "")
            if desc and desc != "DECORATIVE":
                chunks.append(_figure_marker(fig, desc))
        chunks.append("")
    return "\n".join(chunks).strip() + "\n"


def _describe_figures(
    figures: list[FigureBytes],
    settings: Settings,
    describer: Describer,
) -> tuple[dict[tuple[int, int], str], list[FigureRecord], Path | None]:
    """Filter then describe each figure.

    The filter pipeline (:mod:`app.core.figure_filters`) runs first —
    figures flagged as tiny / repeated / ribbon are stamped with
    ``description="DECORATIVE"`` and a ``skip_reason``, and **never sent
    to the LLM**. This cuts the describer cost by ~56% on real corpora.
    """
    descriptions: dict[tuple[int, int], str] = {}
    records: list[FigureRecord] = []
    hash_page_counts = build_hash_page_counts(figures)
    for fig in figures:
        decision = decide(fig, hash_page_counts, settings)
        skip_reason = decision.reason if not decision.keep else ""
        if not decision.keep:
            caption = "DECORATIVE"
            logger.debug(
                "filter: p%d.%d dropped (%s, rel_area=%.4f, aspect=%.1f)",
                fig.page,
                fig.index,
                decision.reason,
                fig.rel_area,
                fig.aspect,
            )
        else:
            try:
                caption = describer(
                    fig.bytes,
                    fig.mime_type,
                    settings,
                    width=fig.width,
                    height=fig.height,
                    context_hint=f"Page {fig.page}, figure {fig.index}",
                )
            except Exception as exc:  # noqa: BLE001
                # Describer failed even after retries. Stamp the record with
                # a distinct skip_reason so operators can find and retry it
                # via `kebab ingest retry-errors`. Don't silently flatten to
                # DECORATIVE — that would lose real content.
                error_msg = str(exc)[:200]
                logger.warning(
                    "figure p%d.%d describer error: %s",
                    fig.page,
                    fig.index,
                    error_msg,
                )
                caption = f"ERROR: {error_msg}"
                skip_reason = "describer_error"

        descriptions[(fig.page, fig.index)] = caption
        records.append(
            FigureRecord(
                page=fig.page,
                index=fig.index,
                path=f"figures/p{fig.page:03d}_f{fig.index:02d}.{fig.extension}",
                mime_type=fig.mime_type,
                width=fig.width,
                height=fig.height,
                description=caption,
                skip_reason=skip_reason,
                rect_width=fig.rect_width,
                rect_height=fig.rect_height,
                page_width=fig.page_width,
                page_height=fig.page_height,
                rel_area=fig.rel_area or None,
                aspect=fig.aspect or None,
                dominant_color_usage=fig.dominant_color_usage,
                content_hash=fig.content_hash,
            )
        )
    return descriptions, records, None


def _write_figures_to_disk(
    figures: list[FigureBytes], records: list[FigureRecord], target_dir: Path
) -> None:
    """Write bytes for kept figures and describer-error records.

    Filter drops (``tiny``/``solid_color``/``repeated``/``ribbon``) are
    deterministic and reproducible from metadata — no need to keep their
    bytes on disk. **Describer errors** (``describer_error``) DO get their
    bytes written because ``kebab ingest retry-errors`` needs to re-feed
    the images to the describer without re-extracting from the PDF.
    """
    fig_dir = target_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    for fig, rec in zip(figures, records, strict=True):
        if rec.skip_reason and rec.skip_reason != "describer_error":
            rec.path = ""  # filter drop — no file on disk
            continue
        (target_dir / rec.path).write_bytes(fig.bytes)


def ingest(
    settings: Settings,
    input_path: Path,
    *,
    describer: Describer = describe_image,
    describe_figures: bool = True,
    force: bool = False,
) -> PdfIngestResult:
    """Copy ``input_path`` to ``raw/documents/`` and synthesize derivatives.

    Idempotent: if the processed output already exists and ``force`` is
    false, the PDF is copied (if missing) and the existing derivatives
    are returned unchanged.
    """
    if not input_path.exists() or not input_path.is_file():
        raise IngestError(f"PDF not found: {input_path}")
    if input_path.suffix.lower() != ".pdf":
        raise IngestError(f"not a .pdf file: {input_path}")

    raw_dir = Path(settings.RAW_DIR) / "documents"
    raw_dir.mkdir(parents=True, exist_ok=True)
    target_pdf = raw_dir / input_path.name

    # Copy the binary into raw/ (if not already there).
    if target_pdf.resolve() != input_path.resolve():
        shutil.copy2(input_path, target_pdf)

    stem = _slug(input_path.stem)
    processed_dir = Path(settings.PROCESSED_DIR) / "documents" / stem
    text_path = processed_dir / "text.md"
    figures_path = processed_dir / "figures.json"

    if text_path.exists() and not force:
        figure_records: list[FigureRecord] = []
        if figures_path.exists():
            raw = json.loads(figures_path.read_text(encoding="utf-8"))
            figure_records = [FigureRecord(**item) for item in raw]
        logger.info("ingest: %s already processed — skipping (use force=True to redo)", stem)
        from app.core.sources.index import load_index, register_source, save_index

        index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
        index = load_index(index_path)
        knowledge_root = Path(settings.KNOWLEDGE_DIR)
        try:
            source_raw_path = str(input_path.resolve().relative_to(knowledge_root.resolve()))
        except ValueError:
            source_raw_path = str(target_pdf.relative_to(knowledge_root))
        register_source(
            index,
            stem=stem,
            raw_path=source_raw_path,
            title=input_path.stem.replace("_", " "),
            tier=1,
            checksum=_sha256(target_pdf),
            adapter="local_pdf",
            path_pattern=getattr(settings, "SOURCE_PATH_PATTERN", None),
        )
        save_index(index, index_path)
        return PdfIngestResult(
            original=target_pdf,
            processed_dir=processed_dir,
            text_path=text_path,
            figures_path=figures_path,
            chars=len(text_path.read_text(encoding="utf-8")),
            figure_count=len(figure_records),
            described_count=sum(
                1
                for r in figure_records
                if r.description
                and r.description != "DECORATIVE"
                and not r.skip_reason
            ),
            labeler_errors=sum(
                1 for r in figure_records if r.skip_reason == "describer_error"
            ),
            skipped=True,
            figures=figure_records,
        )

    extraction = extract(target_pdf, extract_figures=describe_figures)

    descriptions: dict[tuple[int, int], str] = {}
    records: list[FigureRecord] = []
    if describe_figures and extraction.figures:
        descriptions, records, _ = _describe_figures(extraction.figures, settings, describer)

    processed_dir.mkdir(parents=True, exist_ok=True)
    if records:
        _write_figures_to_disk(extraction.figures, records, processed_dir)
        figures_path.write_text(
            json.dumps([r.__dict__ for r in records], indent=2), encoding="utf-8"
        )
    else:
        # Always write an empty figures.json so downstream code can rely on it.
        figures_path.write_text("[]", encoding="utf-8")

    markdown = _render_markdown(extraction, descriptions)
    text_path.write_text(markdown, encoding="utf-8")

    described = sum(1 for r in records if r.description and r.description != "DECORATIVE" and not r.skip_reason)
    labeler_errors = sum(1 for r in records if r.skip_reason == "describer_error")
    logger.info(
        "ingested %s → %s (%d chars, %d figures, %d described, %d labeler errors)",
        input_path.name,
        text_path,
        len(markdown),
        len(records),
        described,
        labeler_errors,
    )
    if labeler_errors:
        logger.warning(
            "%s: %d describer errors — run `kebab ingest retry-errors --stem %s` to recover",
            input_path.name,
            labeler_errors,
            stem,
        )
    from app.core.sources.index import load_index, register_source, save_index

    index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    index = load_index(index_path)
    knowledge_root = Path(settings.KNOWLEDGE_DIR)
    # Use the original input path for the index entry when it lives under
    # knowledge/ — this preserves folder structure for metadata extraction
    # (e.g. grade_10/science/). Fall back to the flat copy otherwise.
    try:
        source_raw_path = str(input_path.resolve().relative_to(knowledge_root.resolve()))
    except ValueError:
        source_raw_path = str(target_pdf.relative_to(knowledge_root))
    register_source(
        index,
        stem=stem,
        raw_path=source_raw_path,
        title=input_path.stem.replace("_", " "),
        tier=1,
        checksum=_sha256(target_pdf),
        adapter="local_pdf",
        path_pattern=getattr(settings, "SOURCE_PATH_PATTERN", None),
    )
    save_index(index, index_path)
    return PdfIngestResult(
        original=target_pdf,
        processed_dir=processed_dir,
        text_path=text_path,
        figures_path=figures_path,
        chars=len(markdown),
        figure_count=len(records),
        described_count=described,
        labeler_errors=labeler_errors,
        skipped=False,
        figures=records,
    )


@dataclass
class RetryResult:
    """Outcome of :func:`retry_errors` on one processed doc."""

    stem: str
    retried: int
    recovered: int
    still_failing: int


def retry_errors(
    settings: Settings,
    stem: str,
    *,
    describer: Describer = describe_image,
) -> RetryResult:
    """Re-run the describer on every ``skip_reason="describer_error"`` record.

    Loads the existing ``figures.json``, re-feeds error figures to the
    describer (which has retry-with-backoff built in), updates the records
    in place, and re-renders ``text.md``. Cheap — image bytes are already
    on disk, so no PDF extraction is needed for the describer calls.
    Re-extracting PDF text is still needed for rendering; that's fast.
    """
    processed_dir = Path(settings.PROCESSED_DIR) / "documents" / stem
    figures_path = processed_dir / "figures.json"
    text_path = processed_dir / "text.md"
    if not figures_path.exists():
        raise IngestError(f"no processed output at {processed_dir}")

    raw = json.loads(figures_path.read_text(encoding="utf-8"))
    records: list[FigureRecord] = [FigureRecord(**item) for item in raw]
    error_indices = [i for i, r in enumerate(records) if r.skip_reason == "describer_error"]
    if not error_indices:
        return RetryResult(stem=stem, retried=0, recovered=0, still_failing=0)

    logger.info("retry_errors: %s — retrying %d error records", stem, len(error_indices))

    recovered = 0
    still_failing = 0
    for idx in error_indices:
        rec = records[idx]
        image_path = processed_dir / rec.path
        if not image_path.exists():
            logger.warning(
                "retry_errors: %s p%d.%d has skip_reason=describer_error but no bytes at %s",
                stem,
                rec.page,
                rec.index,
                image_path,
            )
            still_failing += 1
            continue
        image_bytes = image_path.read_bytes()
        try:
            caption = describer(
                image_bytes,
                rec.mime_type,
                settings,
                width=rec.width,
                height=rec.height,
                context_hint=f"Page {rec.page}, figure {rec.index}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "retry_errors: %s p%d.%d still failing: %s", stem, rec.page, rec.index, exc
            )
            rec.description = f"ERROR: {str(exc)[:200]}"
            still_failing += 1
            continue
        rec.description = caption
        rec.skip_reason = ""  # clear the error — description is now valid
        recovered += 1
        logger.info("retry_errors: %s p%d.%d recovered", stem, rec.page, rec.index)

    # Persist updated records.
    figures_path.write_text(
        json.dumps([r.__dict__ for r in records], indent=2), encoding="utf-8"
    )

    # Re-render text.md so the inline figure markers reflect the new descriptions.
    # Need page text from the source PDF, which is still in raw/documents/.
    raw_pdf = Path(settings.RAW_DIR) / "documents" / (stem.replace("_", " ") + ".pdf")
    # The slug is lossy — try the exact stem first, then fall back to a search.
    if not raw_pdf.exists():
        raw_dir = Path(settings.RAW_DIR) / "documents"
        matches = [
            p
            for p in raw_dir.rglob("*.pdf")
            if _slug(p.stem) == stem
        ]
        if not matches:
            raise IngestError(f"retry_errors: source PDF for stem {stem!r} not found")
        raw_pdf = matches[0]

    # Re-render text.md from the fresh page text + updated figure descriptions.
    # No filter re-decisions; we just emit the current figure records.
    extraction = extract(raw_pdf, extract_figures=False)
    markdown = _render_markdown_from_records(extraction, records)
    text_path.write_text(markdown, encoding="utf-8")

    logger.info(
        "retry_errors: %s — recovered %d, still failing %d",
        stem,
        recovered,
        still_failing,
    )
    return RetryResult(
        stem=stem, retried=len(error_indices), recovered=recovered, still_failing=still_failing
    )


def _render_markdown_from_records(
    extraction: PdfExtraction, records: list[FigureRecord]
) -> str:
    """Re-render text.md from extracted page text + persisted figure records.

    Used by :func:`retry_errors` to rebuild ``text.md`` without re-running
    the filter pipeline. Drops figures that are filtered or still
    erroring; inlines real descriptions at their original pN.M position.
    """
    records_by_page: dict[int, list[FigureRecord]] = {}
    for rec in records:
        records_by_page.setdefault(rec.page, []).append(rec)

    chunks: list[str] = []
    for page in extraction.pages:
        chunks.append(f"## Page {page.page_number}\n")
        if page.text.strip():
            chunks.append(page.text.strip())
            chunks.append("")
        for rec in sorted(records_by_page.get(page.page_number, []), key=lambda r: r.index):
            if rec.skip_reason or rec.description == "DECORATIVE":
                continue
            if rec.description.startswith("ERROR:"):
                continue
            chunks.append(f"[Figure p{rec.page}.{rec.index}: {rec.description}]")
        chunks.append("")
    return "\n".join(chunks).strip() + "\n"


def ingest_tree(
    settings: Settings,
    root: Path,
    *,
    describer: Describer = describe_image,
    describe_figures: bool = True,
    force: bool = False,
) -> list[PdfIngestResult]:
    """Recursively ingest every ``*.pdf`` under ``root``.

    Skips PDFs that are already flat inside ``raw/documents/`` so the
    user can safely point at a mixed tree that contains both fresh
    sources and previously-ingested copies.
    """
    if not root.exists() or not root.is_dir():
        raise IngestError(f"not a directory: {root}")
    raw_dir = (Path(settings.RAW_DIR) / "documents").resolve()
    results: list[PdfIngestResult] = []
    for path in sorted(root.rglob("*.pdf")):
        if path.resolve().parent == raw_dir:
            continue
        results.append(
            ingest(
                settings,
                path,
                describer=describer,
                describe_figures=describe_figures,
                force=force,
            )
        )
    logger.info("ingested %d PDF(s) from %s", len(results), root)
    return results
