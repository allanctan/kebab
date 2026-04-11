# Source Index Design

Deterministic source indexing to replace LLM-guessed citation metadata with real provenance data from ingest.

## Problem

Sources flow through the pipeline as filename stems — loose strings with no stable identity. The generate LLM guesses metadata (tier, author) that ingest already captured in `.meta.json` sidecars. Inline citations use inconsistent formats (`see SCI10_Q1_M5`, `see SCI10_Q1_M5_Evidences_of_Plate_Movement`, `see SCI10_Q1_M5, p8`) making post-processing fragile.

## Design

### Source Index File

`knowledge/.kebab/sources.json`:

```json
{
  "sources": [
    {
      "id": 1,
      "stem": "SCI10_Q1_M1_Plate_Tectonics_and_Geologic_Activities",
      "pdf_path": "raw/documents/SCI10_Q1_M1_Plate Tectonics and Geologic Activities.pdf",
      "title": "SCI10 Q1 M1 Plate Tectonics and Geologic Activities",
      "tier": 1,
      "checksum": "abc123def456...",
      "adapter": "local_pdf",
      "retrieved_at": "2026-04-10T12:00:00"
    }
  ],
  "next_id": 2
}
```

- **ID format**: plain sequential integer (1, 2, 3, ...). No prefix. Deterministic — assigned at ingest time from `next_id` counter.
- **Dedup key**: `stem`. Re-ingesting the same source updates the entry and reuses the ID.
- **`pdf_path`**: relative to `knowledge/`. Single source of truth for locating the raw file.
- **Metadata**: populated from the existing `.meta.json` provenance sidecars that ingest already writes.

### Pipeline Changes

#### Ingest

After writing the raw file and `.meta.json` sidecar, register in `sources.json`:

1. Read existing index (or create `{"sources": [], "next_id": 1}`)
2. Check if `stem` already exists → reuse ID, update metadata from sidecar
3. Otherwise assign `next_id`, increment counter
4. Write back

All three adapters (pdf, csv, web) register through the same path.

#### Organize

Plan `source_files` carries **source IDs** (integers) instead of stem strings. The organize agent's manifest includes source IDs so the LLM can assign them to plan nodes.

`HierarchyNode.source_files` type changes from `list[str]` to `list[int]`.

#### Gaps

Staleness detection compares source IDs instead of stem strings:

- Plan node carries `source_files: list[int]`
- Article frontmatter carries `sources: list[Source]` each with an `id: int` field
- Gaps extracts `{s.id for s in frontmatter.sources}` and compares against the plan's `source_files`
- If the plan has IDs the article doesn't → stale

`source_stems` is dropped — IDs replace this function entirely.

#### Generate

**Before calling LLM:**

1. Load source index
2. Resolve gap's source IDs to full metadata + text content from `processed/documents/<stem>/text.md`
3. Map global source IDs to local footnote numbers (1-based, per article). Pass to LLM as locally numbered sources:

```
Sources:
[^1] SCI10 Q1 M1 Plate Tectonics and Geologic Activities
<text snippet>

[^2] SCI10 Q1 M2 Plate Boundaries
<text snippet>
```

**LLM output schema change:**

`GenerationResult.sources` replaced with `source_ids: list[int]` — the LLM returns which local footnote numbers it cited, not full Source objects.

**LLM citation format:** Obsidian-native footnotes. The LLM writes `[^1]`, `[^2]`, `[^1, p5]` inline. Footnote numbering is local to the article (always starts at 1), not global index IDs.

**Post-processing (deterministic, not LLM):**

1. Inline citations (`[^1]`, `[^2]`) left as-is — Obsidian renders them as superscript links
2. Append footnote definitions at the end of the body, resolving local numbers back to the source index:

```markdown
[^1]: [42] [SCI10 Q1 M1 Plate Tectonics and Geologic Activities](../../../raw/documents/SCI10_Q1_M1_Plate%20Tectonics%20and%20Geologic%20Activities.pdf)
[^2]: [7] [SCI10 Q1 M2 Plate Boundaries](../../../raw/documents/SCI10_Q1_M2_Plate%20Boundaries.pdf)
```

The `[42]` and `[7]` are the global source index IDs for cross-referencing with `sources.json`.

3. Populate frontmatter `sources` from index metadata — real tier, checksum, adapter, retrieved_at
4. Spaces in PDF paths are `%20`-encoded for Obsidian compatibility

**Obsidian rendering:** footnote markers appear as clickable superscripts inline; hovering shows the source title and PDF link; clicking navigates to the footnote. PDF links open in Obsidian's built-in PDF viewer.

**Generate prompt** updated: rule 2 changes from `Cite source titles inline as (see {title})` to `Cite sources using Obsidian footnotes: [^1], [^2], or [^1, p5] for page references. Footnote numbers correspond to the source list provided.`

#### Source Model

Add `id: int` field to `Source`:

```python
class Source(BaseModel):
    id: int = Field(..., description="Source index ID.")
    title: str = Field(..., description="Human-readable title of the source.")
    ...
```

#### Frontmatter

```yaml
sources:
- id: 1
  title: SCI10 Q1 M1 Plate Tectonics and Geologic Activities
  tier: 1
  checksum: abc123def456...
  adapter: local_pdf
  retrieved_at: 2026-04-10T12:00:00
```

- `sources` populated from index (real metadata), not LLM guesses
- `source_stems` dropped — source IDs handle staleness detection

### Cleanup

Remove from `generate.py`:
- `_build_stem_to_pdf()` — replaced by index lookup
- `_linkify_sources()` — replaced by footnote generation

### Files Changed

| File | Change |
|---|---|
| `app/models/source.py` | Add `id: int` field |
| `app/pipeline/ingest/pdf.py` | Register source in index after ingest |
| `app/pipeline/ingest/csv_json.py` | Register source in index after ingest |
| `app/pipeline/ingest/web.py` | Register source in index after ingest |
| `app/core/source_index.py` (new) | `SourceIndex` model, `load_index`, `register_source` |
| `app/core/organize_agent.py` | `HierarchyNode.source_files` type → `list[int]` |
| `app/pipeline/organize.py` | Pass source IDs in manifest to LLM |
| `app/pipeline/gaps.py` | Compare source IDs for staleness, drop `_read_source_stems` |
| `app/pipeline/generate.py` | ID-based source loading, new post-processor, drop `_build_stem_to_pdf`/`_linkify_sources` |
| `app/pipeline/prompts/generate_system.md` | Update citation instructions |
| `app/models/frontmatter.py` | No change (extra="allow" handles source_stems removal) |

### Not Changed

- `.meta.json` sidecars — still written by ingest, still the raw provenance record
- Qdrant schema — `sources` payload field stays the same shape
- `source_stems` in existing articles — generate will overwrite on regeneration

### Edge Cases

- **Missing index**: first ingest creates it. Organize/generate fail fast if index is missing.
- **Deleted raw file**: source stays in index with its ID. Lint agent can flag orphaned sources where `pdf_path` doesn't resolve.
- **Re-ingest with changed content**: same stem → same ID, updated checksum. Gaps detects staleness if checksum changes (future enhancement — currently staleness is ID-set-based only).
