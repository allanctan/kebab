# KEBAB Pipeline Guide

## Overview

KEBAB builds a grounded knowledge base from source documents (PDFs, web pages, datasets). The pipeline transforms raw materials into verified, enriched articles indexed in Qdrant.

```
ingest → organize → gaps → generate → contexts → research → qa → sync
```

Each stage is a CLI command run manually. Stages are idempotent — running one twice produces the same result.

## Stages

### 1. Ingest

```bash
kebab ingest pdf --input knowledge/raw/documents/grade_10/science/
kebab ingest csv --input data.csv
kebab ingest web --url https://example.com/article
```

**What it does:**
- Copies raw files to `knowledge/raw/documents/`
- Extracts text to `knowledge/processed/documents/<stem>/text.md`
- Extracts and describes figures (multimodal LLM) → `figures.json` + `figures/`
- Registers each source in `knowledge/.kebab/sources.json` with a unique ID
- Extracts metadata from folder structure (grade, subject) via `SOURCE_PATH_PATTERN`

**Model used:** `FIGURE_MODEL` (default: gemini-flash) for figure descriptions.

### 2. Organize

```bash
kebab organize --domain Science
kebab organize --domain Science --force  # re-propose hierarchy
```

**What it does:**
- Reads processed text from all indexed sources
- LLM proposes a hierarchy: domain → subdomain → topic → article
- Creates stub markdown files in `knowledge/curated/`
- Saves the plan to `knowledge/.kebab/plan.json`

**Model used:** `ORGANIZE_MODEL` (default: gemini-flash).

### 3. Gaps

```bash
kebab gaps
```

**What it does:**
- Compares the plan against curated articles
- Identifies articles that are stubs (need generating) or stale (sources changed)
- Outputs `knowledge/.kebab/gaps-<timestamp>.json`

**No LLM calls.**

### 4. Generate

```bash
kebab generate
```

**What it does:**
- For each gap, loads source text and figure manifest
- LLM writes a grounded article with Obsidian footnotes (`[^N]`) and figure markers (`[FIGURE:N]`)
- Post-processing: resolves footnotes to PDF links, validates figure markers, copies figures
- Writes the article to the curated path

**Model used:** `GENERATE_MODEL` (default: gemini-flash).

### 5. Contexts

```bash
kebab contexts
```

**What it does:**
- Classifies each article by vertical-specific metadata
- Education: grade level + subject (uses source folder metadata as strong signal)
- Healthcare: evidence grade + specialty + audience
- Policy: jurisdiction + version + status
- Legal: jurisdiction + area of law + authority + year

**Model used:** `CONTEXTS_MODEL` (default: gemini-flash).

### 6. Research

```bash
kebab agent research --all                    # verify + fill gaps
kebab agent research --all --mode content     # verify claims only
kebab agent research --all --mode gaps        # fill research gaps only
kebab agent research SCI-ESC-003 --budget 5   # single article
```

**What it does:**

Two-stage architecture:

1. **Planner** — extracts claims from the article, reads `## Research Gaps`, generates search queries targeting Wikipedia/Tavily
2. **Executor** — searches external sources, classifies each finding:
   - **Confirm** — external source supports a claim → adds footnote
   - **Append** — new relevant information → adds content with citation
   - **Dispute** — external source contradicts a claim → flags in `## Disputes`
3. **Dispute Judge** — verifies flagged disputes are genuine (not phrasing/scope differences)
4. **Gap answering** — fills `## Research Gaps` questions with answers from external sources
5. **Wikipedia images** — downloads and describes relevant images

**Confidence computation:**
- Level 0: no sources
- Level 1: has sources, not yet researched
- Level 2: researched, <70% claims confirmed OR has disputes
- Level 3: researched, ≥70% confirmed, 0 disputes (production gate)
- Level 4: human verified

**Models used:** `RESEARCH_PLANNER_MODEL`, `RESEARCH_EXECUTOR_MODEL`, `RESEARCH_JUDGE_MODEL`.

### 7. Q&A

```bash
kebab agent qa --once    # single pass
kebab agent qa --watch   # continuous loop
```

**What it does:**
- Generates grounded Q&A pairs from article content → `## Q&A`
- Discovers knowledge gaps → `## Research Gaps`
- Gap depth scales by education context (grade level)

**Model used:** `QA_MODEL` (default: gemini-flash).

### 8. Sync

```bash
kebab sync
```

**What it does:**
- Parses frontmatter from all curated articles
- Computes confidence level
- Embeds articles using the embedding model
- Upserts to Qdrant

**Model used:** `EMBEDDING_MODEL` (default: gemini-embedding-001).

### 9. Lint

```bash
kebab agent lint
```

**What it does (no LLM):**
- `missing_sources` — articles with zero sources
- `oversized` — body > 50k tokens
- `broken_prerequisite` — prerequisites not in index
- `stale_verification` — last research > 180 days ago
- `orphan` — no parent_ids
- `below_confidence_gate` — confidence < 3
- `unanswered_gaps` — research gaps not yet answered

## Typical Workflow

### First run
```bash
kebab ingest pdf --input knowledge/raw/documents/
kebab organize --domain Science --force
kebab gaps
kebab generate
kebab contexts
kebab agent research --all --mode content
kebab agent qa --once
kebab agent research --all --mode gaps
kebab sync
kebab agent lint
```

### Incremental (new sources added)
```bash
kebab ingest pdf --input new-file.pdf
kebab organize                          # extends existing plan
kebab gaps                              # finds new/stale articles
kebab generate                          # writes new articles only
kebab contexts
kebab agent research --all --mode content
kebab agent qa --once
kebab agent research --all --mode gaps
kebab sync
```

## Configuration

All settings in `.env` (config) and `.env.local` (secrets):

### Per-operation models
```env
ORGANIZE_MODEL=gemini-pro
GENERATE_MODEL=gpt-5.4-mini
CONTEXTS_MODEL=gpt-5.4-mini
RESEARCH_PLANNER_MODEL=gemini-pro
RESEARCH_EXECUTOR_MODEL=gemini-flash
RESEARCH_JUDGE_MODEL=gemini-pro
QA_MODEL=sonnet-4.6
LINT_MODEL=gemini-flash
FIGURE_MODEL=gemini-flash-lite
```

### Available model aliases
Defined in `app/config/models.yaml`:
- `gemini-flash`, `gemini-flash-lite`, `gemini-pro` (Google)
- `gpt-5.4-mini` (Azure OpenAI)
- `haiku-4.5`, `sonnet-4.6` (AWS Bedrock / Claude)
- `minimax` (MiniMax)

## Article Anatomy

```markdown
---
id: SCI-ESC-003
name: Types of Plate Boundaries
type: article
sources:
- id: 1
  title: SCI10 Q1 M2 Plate Boundaries
  tier: 1
  checksum: 107e44...
  adapter: local_pdf
contexts:
  education:
    grade: 10
    subject: science
    language: en
research_claims_total: 23
external_confirms: 20
dispute_count: 0
researched_at: '2026-04-11'
---

# Types of Plate Boundaries

Article body with inline figures and footnotes[^1].

![Diagram of plate boundaries](figures/types-of-plate-boundaries/p005_f02.jpeg)

[^1]: [1] [SCI10 Q1 M2 Plate Boundaries](../../../raw/documents/grade_10/science/SCI10_Q1_M2_Plate%20Boundaries.pdf)
[^2]: [Plate tectonics](https://en.wikipedia.org/wiki/Plate%20tectonics)

## Q&A

**Q: What are the three types of plate boundaries?**
Divergent, convergent, and transform, each defined by relative plate motion.

## Research Gaps

- **Q: How does slab pull compare to convection as a driving force?**
  **A:** Slab pull is now considered the dominant mechanism...[^3]
- What is the role of paleomagnetism in plate tectonics evidence?

## Disputes

- **Claim**: "Neither new crust is created nor old crust destroyed."
  **Section**: Transform Plate Boundaries, paragraph 1
  **External source**: [Plate tectonics](https://en.wikipedia.org/wiki/Plate%20tectonics)
  **Contradiction**: Source indicates some crust can be created/destroyed at leaky transforms.
```
