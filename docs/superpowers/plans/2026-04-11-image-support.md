# Image Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Incorporate supporting images from source PDFs and Wikipedia into curated articles, with footnote-style referencing and hallucination validation.

**Architecture:** Generate stage builds a figure manifest from `figures.json`, passes figure images + manifest to the LLM via multimodal API, LLM places `[FIGURE:N]` markers, post-processing validates against manifest, copies files, and appends `[fig-N]` definitions. Research stage downloads Wikipedia images and continues numbering.

**Tech Stack:** Python 3.11+, pydantic-ai (multimodal), pymupdf, httpx

**Spec:** `docs/superpowers/specs/2026-04-11-image-support-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `app/core/figures.py` (new) | Load figure manifest, validate markers, copy figures, resolve definitions |
| `app/pipeline/generate.py` | Pass figures to LLM, resolve markers in post-processing |
| `app/pipeline/prompts/generate_system.md` | Add figure placement rule |
| `app/agents/research/executor.py` | Insert image definitions for research images |
| `app/agents/research/agent.py` | Download Wikipedia images during research |
| `app/pipeline/ingest/adapters/wikipedia.py` | Fetch image metadata from MediaWiki API |
| `tests/unit/core/test_figures.py` (new) | Figure manifest, validation, resolution tests |
| `tests/integration/pipeline/test_generate_figures.py` (new) | End-to-end generate with figures |

---

### Task 1: Figure Manifest and Resolution Helpers

**Files:**
- Create: `app/core/figures.py`
- Create: `tests/unit/core/test_figures.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for figure manifest loading, validation, and resolution."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.core.figures import (
    FigureManifest,
    FigureEntry,
    load_figure_manifest,
    resolve_figure_markers,
    copy_figures,
)


class TestLoadFigureManifest:
    def test_loads_useful_figures(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed" / "documents" / "test_doc"
        figures_dir = processed / "figures"
        figures_dir.mkdir(parents=True)
        (figures_dir / "p001_f02.jpeg").write_bytes(b"fake image")
        figures_json = processed / "figures.json"
        figures_json.write_text(json.dumps([
            {"page": 1, "index": 2, "path": "figures/p001_f02.jpeg",
             "description": "Diagram of plates", "skip_reason": "",
             "width": 500, "height": 400, "mime_type": "image/jpeg"},
            {"page": 1, "index": 3, "path": "",
             "description": "DECORATIVE", "skip_reason": "tiny",
             "width": 10, "height": 10, "mime_type": "image/png"},
        ]))
        manifest = load_figure_manifest(processed)
        assert len(manifest.entries) == 1
        assert manifest.entries[0].figure_id == "p001_f02"
        assert manifest.entries[0].description == "Diagram of plates"

    def test_skips_error_descriptions(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed" / "documents" / "test_doc"
        figures_dir = processed / "figures"
        figures_dir.mkdir(parents=True)
        (figures_dir / "p001_f01.jpeg").write_bytes(b"fake")
        figures_json = processed / "figures.json"
        figures_json.write_text(json.dumps([
            {"page": 1, "index": 1, "path": "figures/p001_f01.jpeg",
             "description": "ERROR: API failed", "skip_reason": "describer_error",
             "width": 500, "height": 400, "mime_type": "image/jpeg"},
        ]))
        manifest = load_figure_manifest(processed)
        assert len(manifest.entries) == 0

    def test_empty_when_no_figures_json(self, tmp_path: Path) -> None:
        processed = tmp_path / "processed" / "documents" / "test_doc"
        processed.mkdir(parents=True)
        manifest = load_figure_manifest(processed)
        assert len(manifest.entries) == 0


class TestResolveFigureMarkers:
    def test_resolves_valid_markers(self) -> None:
        manifest = FigureManifest(entries=[
            FigureEntry(local_num=1, figure_id="p001_f02", description="Plates diagram",
                        source_path=Path("/tmp/figures/p001_f02.jpeg"), mime_type="image/jpeg"),
            FigureEntry(local_num=2, figure_id="p003_f01", description="Map",
                        source_path=Path("/tmp/figures/p003_f01.jpeg"), mime_type="image/jpeg"),
        ])
        body = "Intro text.\n\n[FIGURE:1]\n\nMore text.\n\n[FIGURE:2]\n"
        result, used = resolve_figure_markers(body, manifest, "my-article")
        assert "[FIGURE:1]" not in result
        assert "[FIGURE:2]" not in result
        assert "![Plates diagram]" in result
        assert "figures/my-article/p001_f02.jpeg" in result
        assert len(used) == 2

    def test_strips_invalid_markers(self) -> None:
        manifest = FigureManifest(entries=[
            FigureEntry(local_num=1, figure_id="p001_f02", description="Plates",
                        source_path=Path("/tmp/figures/p001_f02.jpeg"), mime_type="image/jpeg"),
        ])
        body = "Text.\n\n[FIGURE:1]\n\n[FIGURE:99]\n"
        result, used = resolve_figure_markers(body, manifest, "slug")
        assert "[FIGURE:99]" not in result
        assert len(used) == 1

    def test_no_markers_returns_unchanged(self) -> None:
        manifest = FigureManifest(entries=[])
        body = "Just text, no figures.\n"
        result, used = resolve_figure_markers(body, manifest, "slug")
        assert result == body
        assert len(used) == 0


class TestCopyFigures:
    def test_copies_used_figures(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "figures"
        src.mkdir(parents=True)
        (src / "p001_f02.jpeg").write_bytes(b"image data")
        dest = tmp_path / "curated" / "figures" / "my-article"

        entries = [
            FigureEntry(local_num=1, figure_id="p001_f02", description="test",
                        source_path=src / "p001_f02.jpeg", mime_type="image/jpeg"),
        ]
        copy_figures(entries, dest)
        assert (dest / "p001_f02.jpeg").exists()
        assert (dest / "p001_f02.jpeg").read_bytes() == b"image data"

    def test_creates_dest_dir(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "img.jpeg").write_bytes(b"data")
        dest = tmp_path / "new" / "dir"
        copy_figures(
            [FigureEntry(local_num=1, figure_id="img", description="x",
                         source_path=src / "img.jpeg", mime_type="image/jpeg")],
            dest,
        )
        assert dest.is_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/test_figures.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement figures module**

Create `app/core/figures.py`:

```python
"""Figure manifest loading, marker validation, and file copying.

Supports the generate stage's figure placement workflow:
1. Load available figures from ``figures.json``
2. Build a numbered manifest for the LLM
3. Validate ``[FIGURE:N]`` markers against the manifest
4. Copy used figures to the article's figures directory
5. Resolve markers to ``![description](path)`` markdown
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_FIGURE_MARKER_RE = re.compile(r"\[FIGURE:(\d+)\]")


@dataclass
class FigureEntry:
    """One available figure in the manifest."""

    local_num: int
    figure_id: str
    description: str
    source_path: Path
    mime_type: str


@dataclass
class FigureManifest:
    """Numbered manifest of available figures for one article."""

    entries: list[FigureEntry] = field(default_factory=list)

    def get(self, local_num: int) -> FigureEntry | None:
        for entry in self.entries:
            if entry.local_num == local_num:
                return entry
        return None

    def prompt_text(self) -> str:
        """Build the manifest text for the LLM prompt."""
        if not self.entries:
            return ""
        lines = ["Available figures:"]
        for e in self.entries:
            lines.append(f"[{e.local_num}] {e.figure_id} — \"{e.description}\"")
        return "\n".join(lines)


def load_figure_manifest(processed_dir: Path) -> FigureManifest:
    """Load useful figures from a processed document directory.

    Skips decorative, filtered, and error figures. Returns a numbered
    manifest ready for the LLM prompt.
    """
    figures_json = processed_dir / "figures.json"
    if not figures_json.exists():
        return FigureManifest()

    raw = json.loads(figures_json.read_text(encoding="utf-8"))
    entries: list[FigureEntry] = []
    num = 1
    for record in raw:
        path = record.get("path", "")
        description = record.get("description", "")
        skip = record.get("skip_reason", "")

        if not path or not description:
            continue
        if description == "DECORATIVE" or description.startswith("ERROR:"):
            continue
        if skip:
            continue

        source_path = processed_dir / path
        if not source_path.exists():
            logger.debug("figures: %s referenced but missing on disk", path)
            continue

        figure_id = Path(path).stem  # e.g. "p001_f02"
        entries.append(FigureEntry(
            local_num=num,
            figure_id=figure_id,
            description=description,
            source_path=source_path,
            mime_type=record.get("mime_type", "image/jpeg"),
        ))
        num += 1

    return FigureManifest(entries=entries)


def resolve_figure_markers(
    body: str,
    manifest: FigureManifest,
    article_slug: str,
) -> tuple[str, list[FigureEntry]]:
    """Replace ``[FIGURE:N]`` markers with image markdown.

    Returns ``(resolved_body, used_entries)``. Invalid markers (N not
    in manifest) are stripped with a warning.
    """
    used: list[FigureEntry] = []

    def _replace(match: re.Match[str]) -> str:
        num = int(match.group(1))
        entry = manifest.get(num)
        if entry is None:
            logger.warning("figures: [FIGURE:%d] not in manifest — stripping", num)
            return ""
        used.append(entry)
        ext = Path(entry.source_path).suffix
        rel_path = f"figures/{article_slug}/{entry.figure_id}{ext}"
        return f"![{entry.description}]({rel_path})"

    resolved = _FIGURE_MARKER_RE.sub(_replace, body)
    return resolved, used


def copy_figures(entries: list[FigureEntry], dest_dir: Path) -> None:
    """Copy figure files to the article's figures directory."""
    if not entries:
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        ext = entry.source_path.suffix
        target = dest_dir / f"{entry.figure_id}{ext}"
        if not entry.source_path.exists():
            logger.warning("figures: source %s not found — skipping copy", entry.source_path)
            continue
        shutil.copy2(entry.source_path, target)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/test_figures.py -v`
Expected: all PASS

- [ ] **Step 5: Type check**

Run: `uv run basedpyright app/core/figures.py`
Expected: 0 errors

---

### Task 2: Wire Figures into Generate Stage

**Files:**
- Modify: `app/pipeline/generate.py`
- Modify: `app/pipeline/prompts/generate_system.md`

- [ ] **Step 1: Update the generate prompt**

Add to the end of `app/pipeline/prompts/generate_system.md`:

```markdown
7. When a figure manifest is provided, place [FIGURE:N] markers where an
   image supports the text. N must be a number from the manifest. Only
   reference figures that exist in the manifest. The system will strip
   any invalid references. Place markers on their own line.
```

- [ ] **Step 2: Update `_load_sources` to also load figures**

In `app/pipeline/generate.py`, add a helper to load figure manifests for all sources of a gap:

```python
from app.core.figures import (
    FigureManifest,
    FigureEntry,
    load_figure_manifest,
    resolve_figure_markers,
    copy_figures,
)


def _load_figures(
    settings: Settings,
    gap: Gap,
    index: SourceIndex,
) -> FigureManifest:
    """Load and merge figure manifests from all sources of a gap."""
    processed_docs = Path(settings.PROCESSED_DIR) / "documents"
    all_entries: list[FigureEntry] = []
    num = 1
    for source_id in gap.source_files:
        try:
            entry = index.get(source_id)
        except KeyError:
            continue
        doc_dir = processed_docs / entry.stem
        manifest = load_figure_manifest(doc_dir)
        for fig in manifest.entries:
            all_entries.append(FigureEntry(
                local_num=num,
                figure_id=fig.figure_id,
                description=fig.description,
                source_path=fig.source_path,
                mime_type=fig.mime_type,
            ))
            num += 1
    return FigureManifest(entries=all_entries)
```

- [ ] **Step 3: Update `_default_proposer` to include figure manifest in the user prompt**

```python
def _default_proposer(
    settings: Settings, gap: Gap, sources: list[tuple[str, str]],
    figure_manifest: FigureManifest | None = None,
) -> GenerationResult:
    agent = build_generate_agent(settings)
    deps = GenerateDeps(settings=settings, gap=gap, sources=sources)
    sources_str = "\n\n".join(f"### {name}\n{snippet}" for name, snippet in sources)
    parts = [
        f"topic_id: {gap.id}",
        f"topic_name: {gap.name}",
        f"topic_description: {gap.description}",
        f"\nsources:\n{sources_str}",
    ]
    if figure_manifest and figure_manifest.entries:
        parts.append(f"\n{figure_manifest.prompt_text()}")
    user = "\n".join(parts)
    return agent.run_sync(user, deps=deps).output
```

Note: for now, pass the manifest as text only. Multimodal (sending actual images) can be a follow-up enhancement — the text descriptions + IDs are sufficient for the LLM to decide placement.

- [ ] **Step 4: Update `run()` to load figures, resolve markers, and copy files**

In the `run()` function, after generating the article body:

```python
        # Load figure manifest for this gap
        figure_manifest = _load_figures(settings, gap, index)

        # ... existing proposer call, token check, etc. ...

        # Resolve figure markers and copy files
        article_slug = path.stem
        body_with_figures, used_figures = resolve_figure_markers(
            body, figure_manifest, article_slug,
        )
        if used_figures:
            figures_dest = path.parent / "figures" / article_slug
            copy_figures(used_figures, figures_dest)
        body = body_with_figures
```

This goes after `body = _append_footnotes(...)` and before `write_article(...)`.

Also update the `GenerateProposer` type alias to accept the optional manifest, and pass `figure_manifest` when calling the proposer.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/ -q --tb=short -m "not expensive and not ai and not network"`
Expected: all pass

- [ ] **Step 6: Type check**

Run: `uv run basedpyright app/pipeline/generate.py app/core/figures.py`
Expected: 0 errors

---

### Task 3: Wikipedia Image Fetching

**Files:**
- Modify: `app/pipeline/ingest/adapters/wikipedia.py`
- Create: `tests/unit/pipeline/test_wikipedia_images.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for Wikipedia image fetching."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config.config import Settings
from app.pipeline.ingest.adapters.wikipedia import WikipediaAdapter, fetch_article_images


def _settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    return Settings(
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )


def _mock_client() -> MagicMock:
    return MagicMock()


class TestFetchArticleImages:
    def test_returns_image_urls(self, tmp_path: Path) -> None:
        client = _mock_client()
        # Mock the images API response
        images_response = MagicMock()
        images_response.json.return_value = {
            "query": {
                "pages": {
                    "12345": {
                        "images": [
                            {"title": "File:Plate boundaries.svg"},
                            {"title": "File:Wiki-logo.png"},
                        ]
                    }
                }
            }
        }
        images_response.raise_for_status = MagicMock()

        # Mock the imageinfo API response
        info_response = MagicMock()
        info_response.json.return_value = {
            "query": {
                "pages": {
                    "-1": {
                        "title": "File:Plate boundaries.svg",
                        "imageinfo": [{
                            "url": "https://upload.wikimedia.org/Plate_boundaries.svg",
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Plate_boundaries.svg",
                            "extmetadata": {
                                "ImageDescription": {"value": "Map of plate boundaries"},
                            },
                        }],
                    }
                }
            }
        }
        info_response.raise_for_status = MagicMock()

        client.get.side_effect = [images_response, info_response, info_response]

        images = fetch_article_images("Plate tectonics", client=client, limit=2)
        assert len(images) >= 1
        assert images[0]["url"].startswith("https://")
        assert "description" in images[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/pipeline/test_wikipedia_images.py -v`
Expected: FAIL — function not found

- [ ] **Step 3: Add `fetch_article_images` to Wikipedia adapter**

In `app/pipeline/ingest/adapters/wikipedia.py`, add:

```python
_IMAGES_URL = (
    "https://en.wikipedia.org/w/api.php"
    "?action=query&prop=images&titles={title}"
    "&format=json&imlimit=50"
)
_IMAGEINFO_URL = (
    "https://en.wikipedia.org/w/api.php"
    "?action=query&titles={file_title}"
    "&prop=imageinfo&iiprop=url|extmetadata"
    "&format=json"
)

# Skip common non-content images
_SKIP_PREFIXES = ("File:Wiki", "File:Commons", "File:Symbol", "File:Icon", "File:Flag")


def fetch_article_images(
    title: str,
    *,
    client: httpx.Client | None = None,
    limit: int = 5,
) -> list[dict[str, str]]:
    """Fetch image URLs and descriptions for a Wikipedia article.

    Returns a list of dicts with 'url', 'description', 'title' keys.
    Skips common non-content images (logos, icons, flags).
    """
    if client is None:
        client = httpx.Client(timeout=30.0, headers={"User-Agent": "kebab/0.1"})

    # Get image list
    url = _IMAGES_URL.format(title=quote(title))
    response = client.get(url)
    response.raise_for_status()
    data = response.json()
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return []
    page = next(iter(pages.values()))
    image_titles = [
        img["title"] for img in page.get("images", [])
        if not any(img["title"].startswith(p) for p in _SKIP_PREFIXES)
    ]

    # Get image info for each
    results: list[dict[str, str]] = []
    for file_title in image_titles[:limit]:
        info_url = _IMAGEINFO_URL.format(file_title=quote(file_title))
        info_resp = client.get(info_url)
        info_resp.raise_for_status()
        info_data = info_resp.json()
        info_pages = info_data.get("query", {}).get("pages", {})
        for info_page in info_pages.values():
            imageinfo = info_page.get("imageinfo", [])
            if not imageinfo:
                continue
            ii = imageinfo[0]
            extmeta = ii.get("extmetadata", {})
            desc = extmeta.get("ImageDescription", {}).get("value", file_title)
            results.append({
                "url": ii.get("url", ""),
                "description": desc,
                "title": file_title,
            })
    return results
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/pipeline/test_wikipedia_images.py -v`
Expected: PASS

- [ ] **Step 5: Type check**

Run: `uv run basedpyright app/pipeline/ingest/adapters/wikipedia.py`
Expected: 0 errors

---

### Task 4: Research Image Integration

**Files:**
- Modify: `app/agents/research/agent.py`
- Modify: `app/agents/research/executor.py`

- [ ] **Step 1: Add image downloading to the research searcher**

In `app/agents/research/agent.py`, update `_default_searcher` to also return image info. After fetching a Wikipedia article, call `fetch_article_images` and include the results:

Add a helper to download and store a Wikipedia image:

```python
def _download_research_image(
    image_url: str,
    description: str,
    article_path: Path,
    article_slug: str,
) -> str | None:
    """Download an image and return its relative markdown path, or None on failure."""
    try:
        response = httpx.get(image_url, timeout=15, follow_redirects=True)
        response.raise_for_status()
    except Exception as exc:
        logger.warning("research: image download failed: %s", exc)
        return None

    ext = Path(image_url.split("?")[0]).suffix or ".png"
    # Slugify the description for filename
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", description.lower().strip())[:40].strip("-")
    filename = f"wiki-{slug}{ext}"

    dest_dir = article_path.parent / "figures" / article_slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    dest.write_bytes(response.content)

    return f"figures/{article_slug}/{filename}"
```

- [ ] **Step 2: Update `apply_findings_to_article` to handle image definitions**

In `app/agents/research/executor.py`, extend `FindingTuple` to optionally carry an image path. When a finding has an associated image, append an image definition alongside the footnote:

After appending a confirm/append footnote, if an image path is provided:
```python
if image_path:
    new_footnote_defs.append(f"\n![{source_title}]({image_path})")
```

This is a lightweight addition — the image goes right after the text it confirms/enriches.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -q --tb=short -m "not expensive and not ai and not network"`
Expected: all pass

- [ ] **Step 4: Type check**

Run: `uv run basedpyright app/agents/research/agent.py app/agents/research/executor.py`
Expected: 0 errors

---

### Task 5: Full Integration Test

**Files:** none (manual verification)

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest tests/ -q --tb=short -m "not expensive and not ai and not network"
```
Expected: all pass

- [ ] **Step 2: Lint and type check**

```bash
uv run ruff check . && uv run basedpyright app/
```
Expected: 0 errors

- [ ] **Step 3: Manual smoke test — generate with figures**

First, ensure figures have descriptions (re-ingest if needed):
```bash
uv run kebab ingest pdf --input "knowledge/raw/documents/grade_10/science/SCI10_Q1_M2_Plate Boundaries.pdf" --force
```

Then rebuild one article:
```bash
# Delete one article to force regeneration
rm knowledge/curated/Science/Earth\ Science/types-of-plate-boundaries.md
uv run kebab gaps
uv run kebab generate
```

Check:
- Article body contains `![description](figures/slug/p001_f02.jpeg)` references
- `figures/` directory created alongside article with copied images
- No `[FIGURE:N]` markers remain in the body (all resolved)

- [ ] **Step 4: Manual smoke test — research with Wikipedia images**

```bash
uv run kebab agent research SCI-ESC-001-001 --budget 3
```

Check:
- Wikipedia images downloaded to `figures/<slug>/wiki-*.png`
- Image markdown inserted near confirmed/appended text
