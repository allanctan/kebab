# Image Support in Curated Articles

Incorporate supporting images into curated articles from source PDFs (generate stage) and external sources (research stage).

## Problem

Figure images are extracted during PDF ingest and stored in `processed/documents/<stem>/figures/` with descriptions. But curated articles never reference them — the educational content is text-only. Images significantly improve comprehension for K-12 material.

## Design

### Image storage

All images for an article are copied to a self-contained directory alongside it:

```
curated/
  Science/
    Earth Science/
      types-of-plate-boundaries.md
      figures/
        types-of-plate-boundaries/
          p001_f02.jpeg          ← from source PDF
          p003_f01.jpeg          ← from source PDF
          wikipedia_subduction.png  ← from research
```

This structure works in Obsidian vaults — images render inline with short relative paths.

### Generate stage changes

**Before calling LLM:**

1. Load figure images + descriptions from `processed/documents/<stem>/figures/` for each source assigned to the article
2. Filter to useful figures only (skip `DECORATIVE` and filtered entries from `figures.json`)
3. Pass figure images to the LLM via multimodal API alongside the source text
4. Include a figure manifest in the prompt: `Available figures: [p001_f02: "diagram of plate boundaries", p003_f01: "map of tectonic plates"]`

**LLM output:**

The LLM places `[FIGURE:p001_f02]` markers in the article body where it wants images. It decides freely — no cap on figure count.

**Post-processing:**

1. For each `[FIGURE:id]` marker in the body:
   - Copy the figure file from `processed/documents/<stem>/figures/<id>.jpeg` to `curated/<path>/figures/<article-slug>/`
   - Replace the marker with `![description](figures/<article-slug>/<id>.jpeg)`
2. Descriptions come from `figures.json`
3. Paths are relative to the article file for Obsidian compatibility

**Image referencing (footnote-style):**

The LLM outputs `[FIGURE:N]` markers (local numbering, like footnotes). Image definitions go at the bottom of the body:

```markdown
The Earth's lithosphere is divided into several tectonic plates[^1].

[FIGURE:1]

At divergent boundaries, plates move apart[^1].

[FIGURE:2]

[fig-1]: ![Diagram of plate boundaries](figures/types-of-plate-boundaries/p005_f02.jpeg)
[fig-2]: ![Map of tectonic plates](figures/types-of-plate-boundaries/p003_f01.jpeg)
```

Research stage continues numbering from where generate left off (same pattern as footnotes).

### Manifest and validation

**Generate manifest:** before calling the LLM, build a numbered manifest of available figures:

```
Available figures:
[1] p001_f02 — "Diagram showing three types of plate boundaries"
[2] p003_f01 — "World map of tectonic plates"
[3] p005_f02 — "Cross-section of subduction zone"
```

The manifest is passed alongside the figure images (multimodal) so the LLM can see both the image and its ID.

**Hallucination guard:** post-processing validates every `[FIGURE:N]` marker against the manifest:
- **N exists in manifest** → copy file to figures directory, append `[fig-N]` definition
- **N does not exist** → strip the marker from the body, log warning

This is the only defense needed. The LLM cannot reference figures outside the manifest because invalid markers are silently dropped.

**Research images:** the executor downloads the image to disk *before* inserting the marker. The file always exists before it's referenced — no hallucination possible.

### Research stage changes

**When fetching Wikipedia articles:**

1. Request image metadata via MediaWiki API: `prop=images` to get image list, `prop=imageinfo` to get URLs + descriptions
2. Match image captions against the claims being confirmed/appended
3. Download relevant images to `curated/<path>/figures/<article-slug>/`
4. Insert `[FIGURE:N]` marker near the confirmed/appended text, append `[fig-N]` definition
5. Continue numbering from where generate left off (check existing `[fig-N]` definitions)
6. Store license metadata (Wikipedia images are CC-BY-SA) in a sidecar `.meta.json` next to the image

**Image selection:** match image caption/filename against the claim text. If the image title contains keywords from the claim, include it.

### Prompt changes

**Generate prompt** (`app/pipeline/prompts/generate_system.md`) — add rule:

```
7. When a figure manifest is provided, place [FIGURE:N] markers where an
   image supports the text. N must be a number from the manifest. Only
   reference figures that exist in the manifest. The system will strip
   any invalid references.
```

**Research executor prompt** — no change needed. Image insertion during research is handled by post-processing based on caption matching, not LLM placement.

### Files changed

| File | Change |
|---|---|
| `app/pipeline/generate.py` | Load figures, pass to multimodal LLM, resolve markers |
| `app/pipeline/prompts/generate_system.md` | Add figure placement rule |
| `app/agents/research/agent.py` | Download Wikipedia images during research |
| `app/agents/research/executor.py` | Insert image markdown near confirmed/appended text |
| `app/pipeline/ingest/adapters/wikipedia.py` | Add `prop=images` + `prop=imageinfo` to fetch |

### Not changed

- Ingest figure extraction — already works
- Figure filter pipeline — already filters decorative images
- `figures.json` format — already has descriptions and paths
- Obsidian rendering — standard markdown images just work

### Edge cases

- **No useful figures:** generate produces no `[FIGURE:]` markers. Article is text-only. Fine.
- **Figure referenced but missing:** post-processing logs a warning and skips the marker.
- **Wikipedia image download fails:** skip silently, log warning. Text enrichment still applies.
- **Large images:** no resizing — Obsidian handles display scaling. Original resolution preserved for quality.
- **Duplicate images across articles:** each article gets its own copy in its figures directory. Storage is cheap; self-containment is more valuable.
