"""Stage 0 ingest paths: PDF, CSV/JSON, web."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pymupdf
import pytest

from app.config.config import Settings
from app.core.errors import IngestError
from app.pipeline.ingest import csv_json as csv_json_ingest
from app.pipeline.ingest import pdf as pdf_ingest
from app.pipeline.ingest import web as web_ingest


def _make_pdf(path: Path, body: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), body)
    doc.save(path)
    doc.close()


def _make_pdf_with_image(path: Path, body: str, image_bytes: bytes) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), body)
    rect = pymupdf.Rect(100, 200, 300, 400)
    page.insert_image(rect, stream=image_bytes)
    doc.save(path)
    doc.close()


def _tiny_png() -> bytes:
    """Build a valid 32x32 two-color PNG via PyMuPDF.

    Uses TWO colors so the solid-color filter rule (R2) does not flag
    it — tests want to drive filter behavior via their own fixtures,
    not trip the content-free-image rule incidentally.
    """
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 32, 32), 0)
    pixmap.set_rect(pixmap.irect, (255, 0, 0))
    # Paint the top half blue so dominant color usage drops to ~50%.
    top_half = pymupdf.IRect(0, 0, 32, 16)
    pixmap.set_rect(top_half, (0, 0, 255))
    return pixmap.tobytes("png")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    return Settings(
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        PROCESSED_DIR=knowledge / "processed",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )


def _no_describer(*_args: object, **_kwargs: object) -> str:
    return "DECORATIVE"


@pytest.mark.integration
def test_pdf_ingest_writes_raw_and_processed(tmp_path: Path, settings: Settings) -> None:
    src = tmp_path / "input.pdf"
    _make_pdf(src, "Hello KEBAB")
    result = pdf_ingest.ingest(settings, src, describer=_no_describer)
    # Raw binary lands under raw/documents/ (unchanged).
    assert result.original.exists()
    assert result.original.parent == Path(settings.RAW_DIR) / "documents"
    # All derivatives live under processed/documents/<stem>/.
    assert result.processed_dir == Path(settings.PROCESSED_DIR) / "documents" / "input"
    assert result.text_path.exists()
    assert result.figures_path.exists()
    assert "Hello KEBAB" in result.text_path.read_text()
    # figures.json exists even when there are no figures.
    assert result.text_path.read_text().startswith("## Page 1")


@pytest.mark.integration
def test_pdf_ingest_is_idempotent_by_default(tmp_path: Path, settings: Settings) -> None:
    calls: list[int] = []

    def _counting_describer(*_args: object, **_kwargs: object) -> str:
        calls.append(1)
        return "DECORATIVE"

    src = tmp_path / "input.pdf"
    _make_pdf(src, "Hello")
    first = pdf_ingest.ingest(settings, src, describer=_counting_describer)
    assert first.skipped is False
    second = pdf_ingest.ingest(settings, src, describer=_counting_describer)
    assert second.skipped is True
    # Describer should not have been called on the second run (no figures in this PDF anyway,
    # so calls stays at 0 — the contract is "no new work done").
    assert len(calls) == 0


@pytest.mark.integration
def test_pdf_ingest_force_reprocesses(tmp_path: Path, settings: Settings) -> None:
    src = tmp_path / "input.pdf"
    _make_pdf(src, "Hello")
    first = pdf_ingest.ingest(settings, src, describer=_no_describer)
    # Corrupt the processed text; force re-extraction should fix it.
    first.text_path.write_text("STALE", encoding="utf-8")
    second = pdf_ingest.ingest(settings, src, describer=_no_describer, force=True)
    assert second.skipped is False
    assert "Hello" in second.text_path.read_text()


@pytest.mark.integration
def test_pdf_ingest_extracts_and_describes_figures(
    tmp_path: Path, settings: Settings
) -> None:
    calls: list[tuple[bytes, str]] = []

    def _stub_describer(
        image_bytes: bytes,
        mime_type: str,
        _settings: Settings,
        *,
        width: int | None = None,
        height: int | None = None,
        context_hint: str | None = None,
    ) -> str:
        calls.append((image_bytes, mime_type))
        return "A red square figure."

    src = tmp_path / "with_figure.pdf"
    _make_pdf_with_image(src, "Body text", _tiny_png())
    result = pdf_ingest.ingest(settings, src, describer=_stub_describer)
    assert result.figure_count >= 1
    assert result.described_count >= 1
    # Inline marker shows up in the rendered text.
    text = result.text_path.read_text()
    assert "A red square figure." in text
    # figures.json round-trips.
    figures_json = json.loads(result.figures_path.read_text())
    assert figures_json[0]["description"] == "A red square figure."
    assert figures_json[0]["path"].startswith("figures/")
    # Image bytes landed on disk under processed/documents/<stem>/figures/.
    image_on_disk = result.processed_dir / figures_json[0]["path"]
    assert image_on_disk.exists()


@pytest.mark.integration
def test_pdf_ingest_records_describer_errors_separately(
    tmp_path: Path, settings: Settings
) -> None:
    """When the describer raises, the record keeps bytes + skip_reason='describer_error'."""

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("simulated failure")

    src = tmp_path / "with_figure.pdf"
    _make_pdf_with_image(src, "Body", _big_png())
    result = pdf_ingest.ingest(settings, src, describer=_boom)

    assert result.figure_count == 1
    assert result.described_count == 0  # no successful descriptions
    assert result.labeler_errors == 1  # counted as labeler error, not decorative

    figs = json.loads(result.figures_path.read_text())
    assert figs[0]["skip_reason"] == "describer_error"
    assert figs[0]["description"].startswith("ERROR: simulated failure")
    # Bytes preserved on disk for retry.
    assert figs[0]["path"].startswith("figures/")
    assert (result.processed_dir / figs[0]["path"]).exists()


@pytest.mark.integration
def test_pdf_retry_errors_recovers_failed_figures(
    tmp_path: Path, settings: Settings
) -> None:
    call_count = {"n": 0}

    def _flaky(*_args: object, **_kwargs: object) -> str:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated transient failure")
        return "A recovered caption."

    src = tmp_path / "flaky.pdf"
    _make_pdf_with_image(src, "Body", _big_png())
    first = pdf_ingest.ingest(settings, src, describer=_flaky)
    assert first.labeler_errors == 1

    # Retry — the describer now succeeds on second call.
    stem = first.processed_dir.name
    retry = pdf_ingest.retry_errors(settings, stem, describer=_flaky)
    assert retry.retried == 1
    assert retry.recovered == 1
    assert retry.still_failing == 0

    # figures.json updated: skip_reason cleared, real description stored.
    figs = json.loads(first.figures_path.read_text())
    assert figs[0]["skip_reason"] == ""
    assert figs[0]["description"] == "A recovered caption."
    # text.md re-rendered with the new caption.
    assert "A recovered caption." in first.text_path.read_text()


@pytest.mark.integration
def test_pdf_retry_errors_noop_when_no_errors(
    tmp_path: Path, settings: Settings
) -> None:
    def _ok(*_args: object, **_kwargs: object) -> str:
        return "A fine caption."

    src = tmp_path / "fine.pdf"
    _make_pdf_with_image(src, "Body", _big_png())
    first = pdf_ingest.ingest(settings, src, describer=_ok)
    assert first.labeler_errors == 0

    retry = pdf_ingest.retry_errors(settings, first.processed_dir.name, describer=_ok)
    assert retry.retried == 0
    assert retry.recovered == 0


@pytest.mark.integration
def test_pdf_ingest_skips_decorative_figures(
    tmp_path: Path, settings: Settings
) -> None:
    def _decorative(*_args: object, **_kwargs: object) -> str:
        return "DECORATIVE"

    src = tmp_path / "dec.pdf"
    _make_pdf_with_image(src, "Body", _tiny_png())
    result = pdf_ingest.ingest(settings, src, describer=_decorative)
    assert result.figure_count == 1
    assert result.described_count == 0
    # No inline marker in the text.
    assert "Figure" not in result.text_path.read_text()


def _big_png() -> bytes:
    """A 400x300 two-color PNG — passes tiny-floor and solid-color rules."""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 400, 300), 0)
    pixmap.set_rect(pixmap.irect, (255, 0, 0))
    pixmap.set_rect(pymupdf.IRect(0, 0, 400, 150), (0, 0, 255))
    return pixmap.tobytes("png")


def _green_png() -> bytes:
    """Distinct-content PNG with two colors (different hash + not solid)."""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 400, 300), 0)
    pixmap.set_rect(pixmap.irect, (0, 200, 0))
    pixmap.set_rect(pymupdf.IRect(0, 0, 400, 150), (200, 200, 0))
    return pixmap.tobytes("png")


@pytest.mark.integration
def test_pdf_ingest_filters_repeated_header_seal(
    tmp_path: Path, settings: Settings
) -> None:
    """A seal repeated on 4 pages must be filtered out without any describer call."""
    seal_bytes = _big_png()
    unique_bytes = _green_png()

    src = tmp_path / "with_repeat.pdf"
    doc = pymupdf.open()
    # Four pages, each with the same seal (same bytes → same hash).
    # Page 3 also has a unique science diagram.
    for page_num in range(4):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {page_num + 1} body")
        page.insert_image(pymupdf.Rect(100, 100, 300, 250), stream=seal_bytes)
        if page_num == 2:
            page.insert_image(pymupdf.Rect(100, 400, 300, 550), stream=unique_bytes)
    doc.save(src)
    doc.close()

    describer_calls: list[str] = []

    def _tracking_describer(
        image_bytes: bytes,
        mime_type: str,
        _settings: Settings,
        *,
        width: int | None = None,
        height: int | None = None,
        context_hint: str | None = None,
    ) -> str:
        # Identify each call so we can verify the filter fires.
        if image_bytes == unique_bytes:
            describer_calls.append("unique")
            return "A unique science diagram."
        describer_calls.append("seal")
        return "should not be called"

    result = pdf_ingest.ingest(settings, src, describer=_tracking_describer)

    # Four seal instances + one unique = 5 total figures.
    assert result.figure_count == 5
    # Filter must have dropped all 4 seal instances (content hash on 4 pages ≥ 3).
    # Only the unique diagram should reach the describer.
    assert describer_calls == ["unique"], f"expected only ['unique'], got {describer_calls}"
    assert result.described_count == 1

    # figures.json should carry skip_reason on the filtered entries.
    figs = json.loads(result.figures_path.read_text())
    reasons = [f["skip_reason"] for f in figs if f["skip_reason"]]
    assert len(reasons) == 4
    assert all(r == "repeated" for r in reasons)
    # Dropped figures have no file on disk (path is empty).
    for fig in figs:
        if fig["skip_reason"]:
            assert fig["path"] == ""

    # text.md contains the unique description but NOT any seal.
    text = result.text_path.read_text()
    assert "A unique science diagram." in text
    assert "should not be called" not in text


@pytest.mark.integration
def test_pdf_ingest_tree_recurses(tmp_path: Path, settings: Settings) -> None:
    root = tmp_path / "sources"
    (root / "grade_9" / "science").mkdir(parents=True)
    (root / "grade_10" / "science").mkdir(parents=True)
    _make_pdf(root / "grade_9" / "science" / "motion.pdf", "Projectile motion")
    _make_pdf(root / "grade_10" / "science" / "tectonics.pdf", "Plate tectonics")
    _make_pdf(root / "grade_10" / "science" / "boundaries.pdf", "Plate boundaries")
    results = pdf_ingest.ingest_tree(settings, root, describer=_no_describer)
    assert len(results) == 3
    names = {r.original.name for r in results}
    assert names == {"motion.pdf", "tectonics.pdf", "boundaries.pdf"}
    # Each source gets its own processed/documents/<stem>/ folder.
    processed_docs = Path(settings.PROCESSED_DIR) / "documents"
    assert (processed_docs / "motion" / "text.md").exists()
    assert (processed_docs / "tectonics" / "text.md").exists()
    assert (processed_docs / "boundaries" / "text.md").exists()


@pytest.mark.integration
def test_pdf_ingest_tree_skips_existing_raw_files(
    tmp_path: Path, settings: Settings
) -> None:
    """Running against the raw tree in-place must not copy files onto themselves."""
    src = tmp_path / "a.pdf"
    _make_pdf(src, "one")
    pdf_ingest.ingest(settings, src, describer=_no_describer)
    assert (Path(settings.RAW_DIR) / "documents" / "a.pdf").exists()

    results = pdf_ingest.ingest_tree(
        settings, Path(settings.RAW_DIR), describer=_no_describer
    )
    assert results == []


@pytest.mark.integration
def test_pdf_ingest_tree_rejects_non_directory(
    tmp_path: Path, settings: Settings
) -> None:
    with pytest.raises(IngestError):
        pdf_ingest.ingest_tree(settings, tmp_path / "missing")


@pytest.mark.integration
def test_pdf_ingest_rejects_missing_file(tmp_path: Path, settings: Settings) -> None:
    with pytest.raises(IngestError):
        pdf_ingest.ingest(settings, tmp_path / "nope.pdf", describer=_no_describer)


@pytest.mark.integration
def test_pdf_ingest_rejects_non_pdf(tmp_path: Path, settings: Settings) -> None:
    bad = tmp_path / "input.txt"
    bad.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(IngestError):
        pdf_ingest.ingest(settings, bad, describer=_no_describer)


@pytest.mark.integration
def test_csv_ingest_copies_file(tmp_path: Path, settings: Settings) -> None:
    src = tmp_path / "rows.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    result = csv_json_ingest.ingest(settings, src)
    assert result.target.exists()
    assert result.kind == "csv"


@pytest.mark.integration
def test_json_ingest_validates_payload(tmp_path: Path, settings: Settings) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"k": "v"}), encoding="utf-8")
    result = csv_json_ingest.ingest(settings, good)
    assert result.target.exists()


@pytest.mark.integration
def test_json_ingest_rejects_invalid_json(tmp_path: Path, settings: Settings) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid", encoding="utf-8")
    with pytest.raises(IngestError):
        csv_json_ingest.ingest(settings, bad)


@pytest.mark.integration
def test_csv_ingest_rejects_unsupported_extension(
    tmp_path: Path, settings: Settings
) -> None:
    bad = tmp_path / "rows.txt"
    bad.write_text("a,b", encoding="utf-8")
    with pytest.raises(IngestError):
        csv_json_ingest.ingest(settings, bad)


@pytest.mark.integration
def test_csv_ingest_tree_mixes_csv_and_json(
    tmp_path: Path, settings: Settings
) -> None:
    root = tmp_path / "datasets"
    (root / "sub").mkdir(parents=True)
    (root / "rows.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (root / "sub" / "data.json").write_text('{"k": 1}', encoding="utf-8")
    (root / "ignore.txt").write_text("ignored", encoding="utf-8")
    results = csv_json_ingest.ingest_tree(settings, root)
    assert {r.kind for r in results} == {"csv", "json"}
    assert len(results) == 2


@pytest.mark.integration
def test_web_ingest_writes_html_and_text(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = "<html><body><h1>Hi</h1><p>kebab</p></body></html>"

    def _fake_get(self: httpx.Client, url: str) -> httpx.Response:
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", _fake_get)
    result = web_ingest.ingest(settings, "https://example.test/page")
    assert result.html_path.exists()
    assert result.text_path.exists()
    assert "kebab" in result.text_path.read_text()


@pytest.mark.integration
def test_web_ingest_propagates_http_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_get(self: httpx.Client, url: str) -> httpx.Response:
        return httpx.Response(500, text="boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", _fake_get)
    with pytest.raises(IngestError):
        web_ingest.ingest(settings, "https://example.test/fail")


@pytest.mark.integration
def test_web_ingest_slug_collision_resistance(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = "<p>x</p>"

    def _fake_get(self: httpx.Client, url: str) -> httpx.Response:
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", _fake_get)
    a = web_ingest.ingest(settings, "https://example.test/a")
    b = web_ingest.ingest(settings, "https://example.test/b")
    assert a.html_path != b.html_path
