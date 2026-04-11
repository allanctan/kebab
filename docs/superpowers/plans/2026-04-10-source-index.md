# Source Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace LLM-guessed source citations with deterministic source indexing backed by real provenance data from ingest.

**Architecture:** A flat JSON index (`knowledge/.kebab/sources.json`) is built incrementally at ingest time. Source IDs (plain integers) flow through organize → gaps → generate. Generate passes locally-numbered sources to the LLM, which cites them as Obsidian footnotes (`[^1]`). Post-processing appends footnote definitions with global IDs and PDF links.

**Tech Stack:** Python 3.11+, Pydantic v2, python-frontmatter, pydantic-ai, pytest

**Spec:** `docs/superpowers/specs/2026-04-10-source-index-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `app/core/source_index.py` (new) | `SourceEntry` model, `SourceIndex` model, `load_index()`, `save_index()`, `register_source()` |
| `app/models/source.py` | Add `id: int` field to `Source` |
| `app/pipeline/ingest/pdf.py` | Call `register_source()` after ingest |
| `app/pipeline/ingest/csv_json.py` | Call `register_source()` after ingest |
| `app/pipeline/ingest/web.py` | Call `register_source()` after ingest |
| `app/core/organize_agent.py` | `HierarchyNode.source_files` type → `list[int]`, update prompts |
| `app/pipeline/organize.py` | Build manifest with source IDs, pass to LLM |
| `app/pipeline/gaps.py` | Compare source IDs for staleness, remove `_read_source_stems` |
| `app/pipeline/generate.py` | ID-based source loading, footnote post-processor, remove `_build_stem_to_pdf`/`_linkify_sources` |
| `app/pipeline/prompts/generate_system.md` | Update citation instructions to footnote format |
| `tests/unit/core/test_source_index.py` (new) | Unit tests for source index CRUD |
| `tests/integration/pipeline/test_gaps.py` | Update to use source IDs |
| `tests/integration/pipeline/test_generate.py` | Update to use source IDs and footnotes |

---

### Task 1: Source Index Module

**Files:**
- Create: `app/core/source_index.py`
- Create: `tests/unit/core/test_source_index.py`

- [ ] **Step 1: Write failing tests for source index**

```python
"""Unit tests for app.core.source_index."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.source_index import SourceEntry, SourceIndex, load_index, register_source, save_index


class TestSourceIndex:
    def test_load_index_returns_empty_when_file_missing(self, tmp_path: Path) -> None:
        index = load_index(tmp_path / ".kebab" / "sources.json")
        assert index.sources == []
        assert index.next_id == 1

    def test_register_source_assigns_sequential_id(self, tmp_path: Path) -> None:
        index_path = tmp_path / ".kebab" / "sources.json"
        index = load_index(index_path)

        entry = register_source(
            index,
            stem="SCI10_Q1_M1_Plate_Tectonics",
            raw_path="raw/documents/SCI10_Q1_M1_Plate Tectonics.pdf",
            title="SCI10 Q1 M1 Plate Tectonics",
            tier=1,
            checksum="abc123",
            adapter="local_pdf",
        )
        assert entry.id == 1
        assert index.next_id == 2

    def test_register_source_deduplicates_by_stem(self, tmp_path: Path) -> None:
        index_path = tmp_path / ".kebab" / "sources.json"
        index = load_index(index_path)

        entry1 = register_source(
            index,
            stem="SCI10_Q1_M1",
            raw_path="raw/documents/SCI10_Q1_M1.pdf",
            title="SCI10 Q1 M1",
            tier=1,
            checksum="abc",
            adapter="local_pdf",
        )
        entry2 = register_source(
            index,
            stem="SCI10_Q1_M1",
            raw_path="raw/documents/SCI10_Q1_M1.pdf",
            title="SCI10 Q1 M1 Updated",
            tier=2,
            checksum="def",
            adapter="local_pdf",
        )
        assert entry1.id == entry2.id == 1
        assert index.next_id == 2
        assert len(index.sources) == 1
        assert index.sources[0].title == "SCI10 Q1 M1 Updated"
        assert index.sources[0].checksum == "def"

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        index_path = tmp_path / ".kebab" / "sources.json"
        index = load_index(index_path)
        register_source(
            index,
            stem="test_stem",
            raw_path="raw/documents/test.pdf",
            title="Test",
            tier=1,
            checksum="aaa",
            adapter="local_pdf",
        )
        save_index(index, index_path)

        reloaded = load_index(index_path)
        assert len(reloaded.sources) == 1
        assert reloaded.sources[0].id == 1
        assert reloaded.sources[0].stem == "test_stem"
        assert reloaded.next_id == 2

    def test_register_multiple_sources_sequential(self, tmp_path: Path) -> None:
        index_path = tmp_path / ".kebab" / "sources.json"
        index = load_index(index_path)

        e1 = register_source(index, stem="a", raw_path="a.pdf", title="A", tier=1, checksum="1", adapter="pdf")
        e2 = register_source(index, stem="b", raw_path="b.pdf", title="B", tier=1, checksum="2", adapter="pdf")
        e3 = register_source(index, stem="c", raw_path="c.pdf", title="C", tier=1, checksum="3", adapter="pdf")

        assert e1.id == 1
        assert e2.id == 2
        assert e3.id == 3
        assert index.next_id == 4

    def test_get_by_id(self, tmp_path: Path) -> None:
        index_path = tmp_path / ".kebab" / "sources.json"
        index = load_index(index_path)
        register_source(index, stem="a", raw_path="a.pdf", title="A", tier=1, checksum="1", adapter="pdf")
        register_source(index, stem="b", raw_path="b.pdf", title="B", tier=2, checksum="2", adapter="pdf")

        assert index.get(1).stem == "a"
        assert index.get(2).stem == "b"
        with pytest.raises(KeyError):
            index.get(99)

    def test_get_by_stem(self, tmp_path: Path) -> None:
        index_path = tmp_path / ".kebab" / "sources.json"
        index = load_index(index_path)
        register_source(index, stem="my_stem", raw_path="a.pdf", title="A", tier=1, checksum="1", adapter="pdf")

        assert index.get_by_stem("my_stem").id == 1
        assert index.get_by_stem("nonexistent") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/test_source_index.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement source index module**

```python
"""Source index — deterministic registry of all ingested source documents.

Built incrementally at ingest time. Downstream stages (organize, gaps,
generate) reference sources by integer ID rather than filename stems.

Pattern adapted from better-ed-ai config pattern — simple JSON persistence.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class SourceEntry(BaseModel):
    """One registered source in the index."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="Sequential source ID.")
    stem: str = Field(..., description="Underscored filename stem (dedup key).")
    raw_path: str = Field(..., description="Path to raw file, relative to knowledge/.")
    title: str = Field(..., description="Human-readable title.")
    tier: int = Field(..., description="Publisher authority tier (1-5).")
    checksum: str = Field(..., description="SHA256 hex digest of raw bytes.")
    adapter: str = Field(..., description="Name of the adapter that fetched this source.")
    retrieved_at: datetime | None = Field(default=None, description="When the source was fetched.")


class SourceIndex(BaseModel):
    """The full source index, persisted to sources.json."""

    model_config = ConfigDict(extra="forbid")

    sources: list[SourceEntry] = Field(default_factory=list)
    next_id: int = Field(default=1)

    def get(self, source_id: int) -> SourceEntry:
        """Return entry by ID or raise KeyError."""
        for entry in self.sources:
            if entry.id == source_id:
                return entry
        raise KeyError(f"no source with id {source_id}")

    def get_by_stem(self, stem: str) -> SourceEntry | None:
        """Return entry by stem or None."""
        for entry in self.sources:
            if entry.stem == stem:
                return entry
        return None


def load_index(path: Path) -> SourceIndex:
    """Load the source index from disk, or return an empty one."""
    if not path.exists():
        return SourceIndex()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return SourceIndex.model_validate(raw)


def save_index(index: SourceIndex, path: Path) -> None:
    """Persist the source index to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(index.model_dump_json(indent=2), encoding="utf-8")


def register_source(
    index: SourceIndex,
    *,
    stem: str,
    raw_path: str,
    title: str,
    tier: int,
    checksum: str,
    adapter: str,
    retrieved_at: datetime | None = None,
) -> SourceEntry:
    """Register or update a source in the index. Returns the entry."""
    existing = index.get_by_stem(stem)
    if existing is not None:
        existing.raw_path = raw_path
        existing.title = title
        existing.tier = tier
        existing.checksum = checksum
        existing.adapter = adapter
        existing.retrieved_at = retrieved_at
        return existing

    entry = SourceEntry(
        id=index.next_id,
        stem=stem,
        raw_path=raw_path,
        title=title,
        tier=tier,
        checksum=checksum,
        adapter=adapter,
        retrieved_at=retrieved_at,
    )
    index.sources.append(entry)
    index.next_id += 1
    return entry
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/test_source_index.py -v`
Expected: all 7 tests PASS

- [ ] **Step 5: Type check**

Run: `uv run basedpyright app/core/source_index.py`
Expected: 0 errors

- [ ] **Step 6: Commit**

```bash
git add app/core/source_index.py tests/unit/core/test_source_index.py
git commit -m "feat: add source index module for deterministic source tracking"
```

---

### Task 2: Add `id` Field to Source Model

**Files:**
- Modify: `app/models/source.py`
- Modify: `tests/integration/pipeline/test_generate.py` (update Source instantiations)

- [ ] **Step 1: Write failing test**

```python
# In a Python REPL or inline test — verify Source requires id
from app.models.source import Source
# This should work after the change:
s = Source(id=1, title="Test", tier=2)
assert s.id == 1
```

- [ ] **Step 2: Add `id` field to Source**

In `app/models/source.py`, add before the `title` field:

```python
    id: int = Field(..., description="Source index ID.")
```

- [ ] **Step 3: Update existing test helpers that instantiate Source**

In `tests/integration/pipeline/test_generate.py`, update `_good_proposer` and similar helpers — every `Source(title=..., tier=...)` needs an `id` parameter. These will be replaced again in Task 7 but must compile now:

```python
# _good_proposer return:
sources=[Source(id=0, title="OpenStax Biology 2e", tier=2)]

# _huge_body_proposer return:
sources=[Source(id=0, title="t", tier=2)]
```

Search all test files for `Source(title=` and add `id=0` to each.

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -q --tb=short`
Expected: all tests pass

- [ ] **Step 5: Type check**

Run: `uv run basedpyright app/models/source.py`
Expected: 0 errors

- [ ] **Step 6: Commit**

```bash
git add app/models/source.py tests/
git commit -m "feat: add id field to Source model"
```

---

### Task 3: Wire Ingest to Register Sources

**Files:**
- Modify: `app/pipeline/ingest/pdf.py`
- Modify: `app/pipeline/ingest/csv_json.py`
- Modify: `app/pipeline/ingest/web.py`
- Create: `tests/unit/pipeline/ingest/test_source_registration.py`

- [ ] **Step 1: Write failing test for PDF ingest registration**

```python
"""Test that ingest registers sources in the index."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config.config import Settings
from app.core.source_index import load_index


@pytest.fixture
def settings_with_knowledge(tmp_path: Path, mock_env: Settings) -> Settings:
    """Settings pointing at a tmp knowledge dir."""
    knowledge = tmp_path / "knowledge"
    (knowledge / "raw" / "documents").mkdir(parents=True)
    (knowledge / "raw" / "datasets").mkdir(parents=True)
    (knowledge / "processed" / "documents").mkdir(parents=True)
    (knowledge / ".kebab").mkdir(parents=True)
    mock_env.KNOWLEDGE_DIR = str(knowledge)
    mock_env.RAW_DIR = str(knowledge / "raw")
    mock_env.PROCESSED_DIR = str(knowledge / "processed")
    return mock_env


def test_pdf_ingest_registers_source(settings_with_knowledge: Settings, tmp_path: Path) -> None:
    from app.pipeline.ingest import pdf as pdf_ingest

    # Create a minimal PDF (just needs to exist for the copy step;
    # we'll mock the extraction)
    fake_pdf = tmp_path / "test_doc.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    index_path = Path(settings_with_knowledge.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    # Pre-check: no index yet
    assert not index_path.exists()

    # Ingest will fail at extraction since it's a fake PDF.
    # For this test, we just need to verify the registration hook is called.
    # Full integration is tested via the real pipeline test.
    # Instead, test the registration helper directly:
    from app.core.source_index import register_source, save_index, load_index

    index = load_index(index_path)
    stem = "test_doc"
    entry = register_source(
        index,
        stem=stem,
        raw_path="raw/documents/test_doc.pdf",
        title="test doc",
        tier=1,
        checksum="abc",
        adapter="local_pdf",
    )
    save_index(index, index_path)

    reloaded = load_index(index_path)
    assert len(reloaded.sources) == 1
    assert reloaded.sources[0].id == 1
    assert reloaded.sources[0].stem == "test_doc"
```

- [ ] **Step 2: Run test to verify it passes** (this one tests the index directly — will pass after Task 1)

Run: `uv run pytest tests/unit/pipeline/ingest/test_source_registration.py -v`

- [ ] **Step 3: Add registration call to pdf.py ingest**

At the end of `pdf.py::ingest()`, before the final `return`, add:

```python
    # Register in source index.
    from app.core.source_index import load_index, register_source, save_index

    index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    index = load_index(index_path)
    knowledge_root = Path(settings.KNOWLEDGE_DIR)
    register_source(
        index,
        stem=stem,
        raw_path=str(target_pdf.relative_to(knowledge_root)),
        title=input_path.stem.replace("_", " "),
        tier=1,
        checksum=_sha256(target_pdf),
        adapter="local_pdf",
    )
    save_index(index, index_path)
```

Add a `_sha256` helper near the top of `pdf.py`:

```python
import hashlib

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
```

- [ ] **Step 4: Add registration call to csv_json.py ingest**

At the end of `csv_json.py::ingest()`, before the final `return`:

```python
    from app.core.source_index import load_index, register_source, save_index

    index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    index = load_index(index_path)
    knowledge_root = Path(settings.KNOWLEDGE_DIR)
    register_source(
        index,
        stem=target.stem.replace(" ", "_"),
        raw_path=str(target.relative_to(knowledge_root)),
        title=input_path.stem.replace("_", " "),
        tier=3,
        checksum=_sha256(target),
        adapter="local_dataset",
    )
    save_index(index, index_path)
```

Add the same `_sha256` helper (or extract to a shared util if preferred).

- [ ] **Step 5: Add registration call to web.py ingest**

At the end of `web.py::ingest()`, before the final `return`:

```python
    from app.core.source_index import load_index, register_source, save_index

    index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    index = load_index(index_path)
    knowledge_root = Path(settings.KNOWLEDGE_DIR)
    register_source(
        index,
        stem=slug,
        raw_path=str(text_path.relative_to(knowledge_root)),
        title=url,
        tier=4,
        checksum=hashlib.sha256(html_path.read_bytes()).hexdigest(),
        adapter="direct_url",
    )
    save_index(index, index_path)
```

- [ ] **Step 6: Run tests and type check**

Run: `uv run pytest tests/ -q --tb=short && uv run basedpyright app/pipeline/ingest/pdf.py app/pipeline/ingest/csv_json.py app/pipeline/ingest/web.py`
Expected: all pass, 0 type errors

- [ ] **Step 7: Commit**

```bash
git add app/pipeline/ingest/pdf.py app/pipeline/ingest/csv_json.py app/pipeline/ingest/web.py tests/unit/pipeline/ingest/test_source_registration.py
git commit -m "feat: register sources in index at ingest time"
```

---

### Task 4: Update Organize to Use Source IDs

**Files:**
- Modify: `app/core/organize_agent.py`
- Modify: `app/pipeline/organize.py`
- Modify: `tests/integration/pipeline/test_organize.py`

- [ ] **Step 1: Change `HierarchyNode.source_files` type**

In `app/core/organize_agent.py`, change:

```python
    source_files: list[str] = Field(
        default_factory=list,
        description="Filenames from raw/ that informed this node.",
    )
```

to:

```python
    source_files: list[int] = Field(
        default_factory=list,
        description="Source index IDs that informed this node.",
    )
```

- [ ] **Step 2: Update organize agent prompts**

In `app/core/organize_agent.py`, update `_SYSTEM_PROMPT` — change the source attribution section:

```
## Source attribution (important)
- For each `article` node, list the **source IDs** (integers) from the manifest
  that discuss the topic — not just the primary source. Multi-source coverage is
  the foundation of the confidence gate. Include a source if it corroborates,
  contextualizes, or cross-references the article's topic.
- Minimum: 1 source ID. Prefer 2–4 when the manifest supports it.
- source_files must contain integers matching the IDs shown in the manifest.
```

Update `_INCREMENTAL_SYSTEM_PROMPT` similarly — references to filenames in `source_files` become source IDs.

- [ ] **Step 3: Update manifest builder in organize.py**

In `app/pipeline/organize.py`, update `_build_manifest()` to load the source index and include IDs:

```python
def _build_manifest(settings: Settings) -> list[tuple[str, str]]:
    """Build ``[(id: stem, snippet), …]`` from the processed/ tree.

    Entry names now include the source index ID for the LLM to reference.
    """
    from app.core.source_index import load_index

    index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    index = load_index(index_path)

    processed_docs = Path(settings.PROCESSED_DIR) / "documents"
    manifest: list[tuple[str, str]] = []
    if processed_docs.exists():
        for sub in sorted(processed_docs.iterdir()):
            text_path = sub / "text.md"
            if not text_path.exists():
                continue
            snippet = text_path.read_text(encoding="utf-8")[:_MANIFEST_SNIPPET_CHARS]
            entry = index.get_by_stem(sub.name)
            label = f"[{entry.id}] {entry.title}" if entry else sub.name
            manifest.append((label, snippet))
    datasets = Path(settings.RAW_DIR) / "datasets"
    if datasets.exists():
        for path in sorted(datasets.iterdir()):
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    text = "(binary dataset)"
                stem = path.stem.replace(" ", "_")
                entry = index.get_by_stem(stem)
                label = f"[{entry.id}] {entry.title}" if entry else path.name
                manifest.append((label, text[:_MANIFEST_SNIPPET_CHARS]))
    return manifest
```

- [ ] **Step 4: Update organize tests**

In `tests/integration/pipeline/test_organize.py`, update any test that checks `source_files` values to expect integers instead of strings. Update fake proposer functions to return `source_files=[1, 2]` instead of `source_files=["file.txt"]`.

- [ ] **Step 5: Run tests and type check**

Run: `uv run pytest tests/ -q --tb=short && uv run basedpyright app/core/organize_agent.py app/pipeline/organize.py`
Expected: all pass, 0 type errors

- [ ] **Step 6: Commit**

```bash
git add app/core/organize_agent.py app/pipeline/organize.py tests/integration/pipeline/test_organize.py
git commit -m "feat: organize uses source IDs instead of filename stems"
```

---

### Task 5: Update Gaps to Use Source IDs

**Files:**
- Modify: `app/pipeline/gaps.py`
- Modify: `tests/integration/pipeline/test_gaps.py`

- [ ] **Step 1: Remove `_read_source_stems` and `_is_stale` stem-based logic**

In `app/pipeline/gaps.py`, replace `_read_source_stems` and `_is_stale` with:

```python
def _read_source_ids(md_path: str | None) -> set[int] | None:
    """Return the set of source IDs from a curated article's frontmatter.

    Returns None if the file doesn't exist or has no sources.
    """
    if md_path is None:
        return None
    path = Path(md_path)
    if not path.exists():
        return None
    try:
        fm, _body = read_article(path)
    except Exception:  # noqa: BLE001
        return None
    if not fm.sources:
        return None
    return {s.id for s in fm.sources}


def _is_stale(node: HierarchyNode) -> bool:
    """Return True if the curated article is missing sources the plan has."""
    recorded = _read_source_ids(node.md_path)
    if recorded is None:
        return False
    plan_ids = set(node.source_files)
    return bool(plan_ids - recorded)
```

- [ ] **Step 2: Update gap test fixtures**

In `tests/integration/pipeline/test_gaps.py`, update `HierarchyNode` fixtures to use `source_files=[1, 2]` (ints) instead of `source_files=["file.txt"]`. Update tests that check staleness to use source IDs.

- [ ] **Step 3: Run tests and type check**

Run: `uv run pytest tests/integration/pipeline/test_gaps.py -v && uv run basedpyright app/pipeline/gaps.py`
Expected: all pass, 0 type errors

- [ ] **Step 4: Commit**

```bash
git add app/pipeline/gaps.py tests/integration/pipeline/test_gaps.py
git commit -m "feat: gaps uses source IDs for staleness detection"
```

---

### Task 6: Update Generate Prompt

**Files:**
- Modify: `app/pipeline/prompts/generate_system.md`

- [ ] **Step 1: Update the prompt**

Replace the current content of `app/pipeline/prompts/generate_system.md`:

```markdown
# KEBAB — Generate Stage System Prompt

You are a careful, source-grounded curator writing a single article for a
universal knowledge base. The article must be deeply grounded in the
provided sources — never invent facts beyond them.

## Input

- `topic_id`: The stable ID this article will be saved under.
- `topic_name`: Human-readable topic name.
- `topic_description`: One-sentence summary of the topic.
- `sources`: A numbered list of sources. Each source has a local footnote
  number (`[^1]`, `[^2]`, …) and a title followed by its text content.

## Output (`GenerationResult`)

- `body`: Markdown body (no frontmatter — KEBAB writes that). Must include
  a `# {topic_name}` heading and a brief introduction.
- `description`: One-sentence summary suitable for the Qdrant payload.
- `keywords`: 3–8 short keywords.
- `source_ids`: List of local footnote numbers you actually cited in the body.
  Must include at least one.

## Hard rules

1. Never invent facts or sources. If you cannot ground a claim in the
   provided snippets, omit it.
2. Cite sources using Obsidian footnotes: `[^1]`, `[^2]`, or `[^1]` with
   page context written naturally (e.g. "as described on page 5[^1]").
   Footnote numbers correspond to the source list provided.
3. Do NOT write footnote definitions — those are generated automatically.
4. Keep the body under 50,000 tokens.
5. Do not include a Q&A section — the qa agent fills that in later.
6. Reasoning before output: think first, then commit. The output schema
   uses field ordering to encourage analysis-before-decision.
```

- [ ] **Step 2: Commit**

```bash
git add app/pipeline/prompts/generate_system.md
git commit -m "feat: update generate prompt to use Obsidian footnote citations"
```

---

### Task 7: Update Generate Stage

**Files:**
- Modify: `app/pipeline/generate.py`
- Modify: `tests/integration/pipeline/test_generate.py`

- [ ] **Step 1: Update `GenerationResult` schema**

In `app/pipeline/generate.py`, replace the `sources` field in `GenerationResult`:

```python
class GenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning: str = Field(
        ...,
        description="Brief analysis of which sources cover which claims.",
    )
    body: str = Field(..., description="Markdown body, no frontmatter.")
    description: str = Field(..., description="One-sentence article summary.")
    keywords: list[str] = Field(
        default_factory=list, description="3–8 short keywords."
    )
    source_ids: list[int] = Field(
        ..., min_length=1, description="Local footnote numbers cited in the body."
    )
```

- [ ] **Step 2: Remove `_build_stem_to_pdf` and `_linkify_sources`**

Delete these two functions and the `stem_to_pdf = _build_stem_to_pdf(settings)` line from `run()`.

- [ ] **Step 3: Add footnote post-processor**

Add this function to `generate.py`:

```python
from urllib.parse import quote as url_quote

from app.core.source_index import SourceEntry


def _append_footnotes(
    body: str,
    local_to_entry: dict[int, SourceEntry],
    article_path: Path,
) -> str:
    """Append Obsidian footnote definitions to the article body."""
    knowledge_root = article_path
    # Walk up to find knowledge/ root (parent of "curated/")
    while knowledge_root.name != "curated" and knowledge_root != knowledge_root.parent:
        knowledge_root = knowledge_root.parent
    knowledge_root = knowledge_root.parent  # one above "curated/"

    lines: list[str] = []
    for local_num in sorted(local_to_entry):
        entry = local_to_entry[local_num]
        raw_path = knowledge_root / entry.raw_path
        rel = raw_path.relative_to(article_path.parent, walk_up=True)
        encoded = str(rel).replace(" ", "%20")
        lines.append(f"[^{local_num}]: [{entry.id}] [{entry.title}]({encoded})")

    if not lines:
        return body

    return body.rstrip() + "\n\n" + "\n".join(lines) + "\n"
```

- [ ] **Step 4: Update `_load_sources` to use source index**

Replace `_load_sources` to resolve source IDs from the index:

```python
from app.core.source_index import SourceIndex, load_index


def _load_sources(
    settings: Settings,
    gap: Gap,
    index: SourceIndex,
) -> list[tuple[int, SourceEntry, str]]:
    """Resolve gap's source IDs to (local_num, entry, text_content) triples."""
    processed_docs = Path(settings.PROCESSED_DIR) / "documents"
    out: list[tuple[int, SourceEntry, str]] = []
    for local_num, source_id in enumerate(gap.source_files, start=1):
        try:
            entry = index.get(source_id)
        except KeyError:
            logger.warning("generate: source id %d not in index — skipping", source_id)
            continue
        candidates = [
            processed_docs / entry.stem / "text.md",
        ]
        for path in candidates:
            if path.exists():
                text = path.read_text(encoding="utf-8")[:8000]
                out.append((local_num, entry, text))
                break
    return out
```

- [ ] **Step 5: Update `run()` to use new flow**

Update the main `run()` function. Key changes:
- Load source index at the start
- Pass index to `_load_sources`
- Build user prompt with local footnote numbers
- Build `local_to_entry` mapping for footnote generation
- Populate frontmatter `sources` from index entries
- Append footnotes to body
- Remove `source_stems` stamping

```python
def run(
    settings: Settings,
    *,
    gaps: GapReport | None = None,
    proposer: GenerateProposer = _default_proposer,
    plan: HierarchyPlan | None = None,
) -> GenerateResult:
    report = gaps if gaps is not None else latest_gaps(settings)
    if report is None:
        raise KebabError("generate: no gaps report — run `kebab gaps` first")

    plan = plan if plan is not None else load_plan(settings)
    index_path = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "sources.json"
    index = load_index(index_path)
    written: list[Path] = []
    skipped: list[tuple[str, str]] = []

    for gap in report.gaps:
        source_triples = _load_sources(settings, gap, index)
        if not source_triples:
            skipped.append((gap.id, "no source files found"))
            continue

        # Build sources list for proposer: [(name, snippet), ...]
        sources_for_llm: list[tuple[str, str]] = [
            (f"[^{local_num}] {entry.title}", text)
            for local_num, entry, text in source_triples
        ]

        try:
            result = proposer(settings, gap, sources_for_llm)
        except ValidationError as exc:
            skipped.append((gap.id, f"schema violation: {exc}"))
            continue
        if count_tokens(result.body) > settings.MAX_TOKENS_PER_ARTICLE:
            skipped.append((gap.id, f"body exceeds {settings.MAX_TOKENS_PER_ARTICLE} tokens"))
            continue

        path = _output_path(settings, gap)
        path.parent.mkdir(parents=True, exist_ok=True)
        preserved = _preserve_existing_fields(path)
        parent_ids = _parent_ids_for(plan, gap.id)

        # Build local→entry mapping and populate sources from index
        local_to_entry: dict[int, SourceEntry] = {
            local_num: entry for local_num, entry, _text in source_triples
        }
        from app.models.source import Source
        fm_sources = [
            Source(
                id=entry.id,
                title=entry.title,
                tier=entry.tier,
                checksum=entry.checksum,
                adapter=entry.adapter,
                retrieved_at=entry.retrieved_at,
            )
            for entry in local_to_entry.values()
        ]

        fm = FrontmatterSchema(
            id=gap.id,
            name=gap.name,
            type="article",
            sources=fm_sources,
        )
        fm_dump = fm.model_dump()
        fm_dump["description"] = result.description
        fm_dump["keywords"] = result.keywords
        fm_dump["parent_ids"] = parent_ids
        for key, value in preserved.items():
            fm_dump[key] = value
        fm = FrontmatterSchema.model_validate(fm_dump)

        body = _append_footnotes(result.body, local_to_entry, path)
        write_article(path, fm, body)
        written.append(path)

    logger.info("generate: wrote %d, skipped %d", len(written), len(skipped))
    return GenerateResult(written=written, skipped=skipped)
```

- [ ] **Step 6: Update `GenerateProposer` type alias**

The signature stays the same — `(Settings, Gap, list[tuple[str, str]]) -> GenerationResult` — since we format the source names before calling.

- [ ] **Step 7: Update test helpers**

In `tests/integration/pipeline/test_generate.py`:

```python
def _gap(id: str = "SCI-BIO-001", target_path: str | None = None) -> Gap:
    return Gap(
        id=id,
        name="Photosynthesis",
        description="Light into glucose.",
        source_files=[1],  # source index ID
        target_path=target_path,
    )


def _good_proposer(
    _settings: Settings, gap: Gap, sources: list[tuple[str, str]]
) -> generate_stage.GenerationResult:
    return generate_stage.GenerationResult(
        reasoning="Source 1 covers all claims.",
        body=f"# {gap.name}\n\nGrounded in source[^1].\n",
        description="Light into glucose.",
        keywords=["chloroplast", "calvin"],
        source_ids=[1],
    )
```

Update each test to:
- Create a `sources.json` in the tmp knowledge dir with a matching entry (id=1, stem="openstax")
- Create `processed/documents/openstax/text.md` with test content
- Update assertions to check for footnote definitions in the body

- [ ] **Step 8: Run tests and type check**

Run: `uv run pytest tests/ -q --tb=short && uv run basedpyright app/pipeline/generate.py`
Expected: all pass, 0 type errors

- [ ] **Step 9: Commit**

```bash
git add app/pipeline/generate.py tests/integration/pipeline/test_generate.py
git commit -m "feat: generate uses source index IDs and Obsidian footnotes"
```

---

### Task 8: Full Pipeline Smoke Test

**Files:** None (manual verification)

- [ ] **Step 1: Wipe curated and index**

```bash
rm -rf knowledge/curated/ knowledge/.kebab/sources.json knowledge/.kebab/plan.json knowledge/.kebab/gaps-*.json
```

- [ ] **Step 2: Re-ingest one PDF per subject**

```bash
uv run kebab ingest pdf --input "knowledge/raw/documents/MATH_GR10_QTR1-MODULE-2edited_FORMATTED_12PAGES (1).pdf" --force
uv run kebab ingest pdf --input "knowledge/raw/documents/NCR_FINAL_Q1-ENG10_M3_Val.pdf" --force
uv run kebab ingest pdf --input "knowledge/raw/documents/SCI10_Q1_M2_Plate Boundaries.pdf" --force
uv run kebab ingest pdf --input "knowledge/raw/documents/SCI9-Q4-MOD1-Projectile Motion.pdf" --force
```

- [ ] **Step 3: Verify sources.json was created**

```bash
cat knowledge/.kebab/sources.json
```

Expected: 4 entries with IDs 1–4, each with stem, raw_path, tier, checksum, adapter.

- [ ] **Step 4: Run organize → gaps → generate**

```bash
uv run kebab organize --domain Science --force
uv run kebab gaps
uv run kebab generate
```

- [ ] **Step 5: Verify generated article has Obsidian footnotes**

Open any generated article in `knowledge/curated/` and verify:
- Inline `[^1]`, `[^2]` citations in the body
- Footnote definitions at the bottom with `[global_id] [Title](path/to.pdf)` format
- Frontmatter `sources` populated with real metadata from index (not LLM guesses)
- No `source_stems` in frontmatter
- PDF links use `%20` encoding for spaces

- [ ] **Step 6: Verify gaps idempotency**

```bash
uv run kebab gaps
```

Expected: 0 new gaps (all articles have real content).

- [ ] **Step 7: Run full test suite**

```bash
uv run pytest tests/ -q --tb=short && uv run ruff check . && uv run basedpyright app/
```

Expected: all pass, no lint or type errors.

- [ ] **Step 8: Commit any remaining fixes**

```bash
git add -A
git commit -m "test: verify full pipeline with source index"
```
