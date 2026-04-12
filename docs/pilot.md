# KEBAB Pilot — K-12 Science

End-to-end walkthrough for setting up and running KEBAB on educational
content. Uses the K-12 Science vertical as the example.

## Prerequisites

```bash
uv sync
```

Create `.env.local` with your API keys:

```env
KEBAB_GOOGLE_API_KEY=...          # Google AI Studio (required — Gemini)
KEBAB_TAVILY_API_KEY=...          # Tavily web search (optional — for research)
```

## Layout

```
knowledge/
├── raw/           ← untouched sources (PDFs, web fetches)
│   ├── documents/
│   │   └── grade_10/science/*.pdf
│   └── web/
│       └── *.md
├── processed/     ← extracted text + described figures
│   └── documents/
│       └── <stem>/
│           ├── text.md
│           ├── figures.json
│           └── figures/
├── curated/       ← the knowledge base — grounded markdown articles
│   └── <Domain>/<Subdomain>/<article>.md
├── .kebab/        ← pipeline state (plan, sources.json, skip keywords)
└── .qdrant/       ← local vector index (derived, rebuildable)
```

## Source path metadata (optional)

If your PDFs are organized by grade and subject, set `SOURCE_PATH_PATTERN`
in `.env` so ingest extracts metadata automatically:

```env
# Matches: raw/documents/grade_10/science/filename.pdf
SOURCE_PATH_PATTERN=raw/documents/grade_{grade}/{subject}/{filename}
```

This passes `grade` and `subject` to the generate stage, which writes
grade-appropriate content (e.g. "Write for grade 10 science students").
Skip this for web-crawled content with no folder structure.

## Pipeline

Run each stage in order. Every stage is idempotent — re-running is safe.

### 1. Ingest

```bash
# PDF — single file or whole folder (recursive)
uv run kebab ingest pdf --input knowledge/raw/documents/

# Web page
uv run kebab ingest web --url https://example.com/article

# Re-process after changing figure filters or describer prompt
uv run kebab ingest pdf --input knowledge/raw/documents/ --force

# Retry failed figure descriptions
uv run kebab ingest retry-errors --stem SCI10_Q1_M2_Plate_Boundaries
```

### 2. Organize

```bash
uv run kebab organize --domain Knowledge
# Creates: .kebab/plan-knowledge.json + stub articles under curated/

# Re-propose from scratch (costs LLM calls)
uv run kebab organize --domain Knowledge --force
```

The domain name becomes the top-level folder under `curated/` and the
plan filename. Use the same domain name in all subsequent commands.

### 3. Generate

```bash
uv run kebab generate --domain Knowledge          # generate new (gap) articles
uv run kebab generate --domain Knowledge --force   # regenerate all articles
uv run kebab generate KNO-SCI-112                  # regenerate a single article
```

Runs three steps internally:
1. **Gaps** — finds stub articles that need writing
2. **Write** — LLM generates grounded markdown with footnotes and figures
3. **Contexts** — classifies each article by vertical (education, healthcare,
   legal, policy) and populates metadata (grade, subject, bloom level, etc.)

### 4. Research (claim verification)

```bash
uv run kebab research KNO-SCI-112              # single article
uv run kebab research --all                    # all articles
uv run kebab research --domain Knowledge       # all articles in a domain
uv run kebab research --all --budget 5         # limit queries per article
```

Verifies article claims against external sources (Wikipedia, Tavily).
Adds footnote citations for confirmed claims, appends new information,
and flags contradictions in a `## Disputes` section.

### 5. Q&A enrichment

```bash
uv run kebab agent qa --once    # single pass over all articles
```

Generates grounded Q&A pairs → `## Q&A` section.
Discovers knowledge gaps → `## Research Gaps` section.

### 6. Research gaps + images

```bash
uv run kebab research-gaps KNO-SCI-112         # single article
uv run kebab research-gaps --all               # all articles
uv run kebab research-gaps --domain Knowledge  # all in domain
uv run kebab research-images --all             # add Wikipedia figures
uv run kebab research-images --domain Knowledge
```

`research-gaps` answers unanswered questions in `## Research Gaps`.
`research-images` downloads and describes figures from Wikipedia articles
cited in the footnotes (requires research to have run first).

### 7. Sync to Qdrant

```bash
uv run kebab sync
```

Embeds articles and upserts to Qdrant. Computes confidence level per
article. Idempotent — unchanged articles are skipped.

### 8. Lint

```bash
uv run kebab agent lint
```

Health checks (no LLM): missing sources, oversized articles, stale
verification, orphaned articles, unanswered gaps, below confidence gate.

## Typical first-run sequence

```bash
uv run kebab ingest pdf --input knowledge/raw/documents/
uv run kebab organize --domain Knowledge
uv run kebab generate --domain Knowledge
uv run kebab research --all
uv run kebab agent qa --once
uv run kebab research-gaps --all
uv run kebab research-images --all
uv run kebab sync
uv run kebab agent lint
```

## Incremental (new sources added)

```bash
uv run kebab ingest pdf --input new-file.pdf
uv run kebab organize --domain Knowledge      # extends existing plan
uv run kebab generate --domain Knowledge      # writes new articles only
uv run kebab research --all
uv run kebab research-gaps --all
uv run kebab research-images --all
uv run kebab sync
```

## Cost & runtime notes

- A full pilot run (~10 articles) hits the Gemini API ~50-100 times.
  With `gemini-2.5-flash` the cost is typically under $0.50.
- Each eval suite documents its expected cost in its docstring.
- Set `LLM_MAX_RETRIES=3` in `.env` to keep retries bounded.

## Per-operation model configuration

Each pipeline stage can use a different LLM. Set in `.env`:

```env
ORGANIZE_MODEL=gemini-flash
GENERATE_MODEL=gemini-flash
CONTEXTS_MODEL=gemini-flash
RESEARCH_PLANNER_MODEL=gemini-flash
RESEARCH_EXECUTOR_MODEL=gemini-flash
RESEARCH_JUDGE_MODEL=gemini-flash
QA_MODEL=gemini-flash
FIGURE_MODEL=gemini-flash-lite
```

Model aliases are defined in `app/config/models.yaml`. Use `provider:model`
syntax for non-aliased models (e.g. `google-gla:gemini-2.5-pro`).
