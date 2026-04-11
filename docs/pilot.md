# KEBAB Pilot — K-12 Science: Photosynthesis

End-to-end walkthrough of the curated knowledge base for the pilot
vertical (Education / K-12 Science / Photosynthesis). Verified by
`tests/integration/pipeline/test_pilot_end_to_end.py` against stubbed
Gemini calls; the same commands run against the real Google AI Studio
API once `KEBAB_GOOGLE_API_KEY` is set.

## Prerequisites

```bash
uv sync
export KEBAB_GOOGLE_API_KEY=...   # Google AI Studio key
```

## Layout

KEBAB follows medallion architecture (bronze → silver → gold):

```
knowledge/
├── raw/           ← untouched binaries (you put sources here)
│   └── documents/
│       └── *.pdf
├── processed/     ← synthesized derivatives (extracted text + described figures)
│   └── documents/
│       └── <stem>/
│           ├── text.md
│           ├── figures.json
│           └── figures/
├── curated/       ← the actual knowledge base — markdown + domain tree
│   └── Science/...
├── .kebab/        ← pipeline state (plan.json, gaps-*, lint-*)
└── .qdrant/       ← derived vector index
```

Place real PDFs under `knowledge/raw/documents/` (any nested layout is fine — ingest recursively walks).

## Pipeline

Run each stage in order. Every stage logs progress to stdout and writes
intermediate artifacts under `knowledge/.kebab/`.

```bash
# Stage 0 — ingest raw sources (pass a single PDF or a whole folder)
uv run kebab ingest pdf --input knowledge/raw/documents/grade_10

# Stage 1 — propose (or load) the canonical hierarchy
uv run kebab organize --domain Science
# Re-running is a no-op — the plan is cached under knowledge/.kebab/plan.json.
# Use --force to re-propose from scratch (spends real LLM calls).

# Stage 2 — diff the plan against the live index
uv run kebab gaps

# Stage 3 — LLM-generate grounded markdown for each gap, at the plan-reserved path
uv run kebab generate

# Stage 4 — populate K-12 grade context
uv run kebab contexts

# Stage 5 — multi-LLM verification
uv run kebab verify

# Stage 6 — embed + upsert into Qdrant
uv run kebab sync
```

## Continuous agents

```bash
uv run kebab agent qa --once          # one enrichment pass
uv run kebab agent lint               # health check report
```

## Manual checks

```bash
uv run kebab status
uv run kebab tree Science
uv run kebab search "light reactions"
uv run kebab check SCI-BIO-002
```

## Acceptance criteria

- The Photosynthesis article reaches `confidence_level == 3` (≥2 sources +
  ≥2 passing verifiers) after the verify + sync stages.
- The qa agent appends at least one new grounded `**Q:` pair to the
  article body that does not duplicate existing questions.
- `uv run kebab agent lint` reports zero issues for the pilot tree.
- `uv run kebab eval generation` passes the committed
  `evals/suites/generation_baseline.json` floor.

## Cost & runtime notes

- A full pilot run hits the Gemini API ~30 times. With `gemini-2.5-flash`
  the typical cost is well under \$0.10.
- Each eval suite documents its expected cost in its module docstring.
- Set `KEBAB_LLM_MAX_RETRIES=3` to keep retries bounded if the API is flaky.
