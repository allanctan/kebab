# DepEd Curriculum Spine — Design Spec

**Date:** 2026-05-22
**Status:** Draft
**Branch:** `feature/deped-curriculum`

## Summary

Make the **DepEd MATATAG curriculum** the spine of the KB. Ingest a
structured list of learning competencies (LCs) from a DepEd-derived
XLSX, store it as a first-class artifact under
`knowledge/.kebab/curriculum/<name>.yaml`, tag curated articles with
the LC codes they cover, and produce a reverse coverage index
(competency → list of articles).

Scope (this spec): **Grade 10, all subjects, MATATAG-aligned**, serving
teachers and students. Articles remain *one per topic* (existing
pattern), but gain a `competency_codes: list[str]` frontmatter field
and a `curriculum: str` tag.

## Problem

KEBAB has no notion of curriculum. Articles exist as a flat tree of
topic markdown. Teachers and students who navigate by competency code
(e.g., `EN10LIT-I-2c`) can't find anything; "is this competency
covered?" is unanswerable.

The user has a structured XLSX
(`MATATAG_Learning_Objectives_Database.xlsx`, 883 rows across K–G12,
~153 G10 rows) derived from official DepEd MATATAG curriculum guides
plus forward-projected G10 rows for SY 2027–28. This is the best
available spine.

We need to (a) ingest it as data, (b) tag articles against it, and
(c) report coverage.

## Solution

### Three new pieces

1. **Curriculum spine YAML.** `knowledge/.kebab/curriculum/<name>.yaml`
   stores the structured competency list. Built once per refresh from
   the XLSX. Source of truth for what should be covered.
2. **Frontmatter additions** on curated articles:
   - `competency_codes: list[str]` — LC codes this article covers
   - `curriculum: str` — name of the spine (e.g., `"matatag-g10-draft"`)
3. **Coverage index JSON.** `knowledge/.kebab/curriculum/<name>.coverage.json`
   maps each LC code → list of article IDs that teach it, plus summary
   stats. Built by walking `curated/` and reading frontmatter.

### Why not extend the existing vertical YAML?

Verticals are about *audience kind* (education vs legal vs healthcare),
not about *which curriculum within an audience*. A single education
vertical may carry several curricula side-by-side (MATATAG, MELC,
Cambridge, IB) over time. The curriculum is its own artifact, keyed by
name, with its own lifecycle.

### Why not bake LC codes into the universal Article model?

`app/models/article.py` is the 12-field universal Qdrant payload —
unchanged for every vertical (CLAUDE.md §1 invariant). Curriculum
codes belong in `contexts` (vertical-specific, passed through via
`extra="allow"` in `FrontmatterSchema`). The `education.yaml`
`classification_fields` is the right place to *declare* them so the
contexts agent classifies new articles correctly.

## Architecture

### File additions

```
app/
  agents/
    curriculum/
      __init__.py
      curriculum.py          # run() orchestrator for `kebab curriculum ingest`
      parser.py              # XLSX → list[Competency]
      coverage.py            # walk curated/ → coverage JSON
      prompts/               # (none yet — pure plumbing, no LLM)
  models/
    curriculum.py            # Competency, CurriculumSpine, CompetencyCoverage
knowledge/
  raw/
    curriculum/
      matatag-learning-objectives-2026-05.xlsx   # canonical source
  .kebab/
    curriculum/
      matatag-g10-draft.yaml            # the spine
      matatag-g10-draft.coverage.json   # reverse index
tests/
  unit/
    agents/
      curriculum/
        test_parser.py
        test_coverage.py
        fixtures/sample.xlsx
```

### Data shapes

**`app/models/curriculum.py`** (Pydantic v2, `extra="forbid"`):

```python
class Competency(BaseModel):
    code: str                          # e.g. "EN10LIT-I-2c" or "SCI10-PT-I-2"
    curriculum: str                    # "MATATAG"
    subject: str                       # "Science"
    grade: str                         # "10"
    key_stage: str | None              # "KS4" — optional, derived
    quarter: str | None                # "Q1"
    domain: str | None                 # "Plate Tectonics"
    subdomain: str | None              # finer category if present
    competency: str                    # full LC sentence
    content_standard: str | None
    performance_standard: str | None
    source_pdf_url: str | None         # DepEd CG URL if known


class CurriculumSpine(BaseModel):
    name: str                          # "matatag-g10-draft"
    curriculum: str                    # "MATATAG"
    source_file: str                   # relative path under knowledge/raw/curriculum/
    generated_at: date_type
    grade_filter: str | None           # if built for a single grade, e.g. "10"
    competencies: list[Competency]
```

**Spine YAML on disk** is `CurriculumSpine.model_dump()` dumped as YAML
(deterministic key order, UTF-8, no anchors).

**`<name>.coverage.json`:**

```json
{
  "name": "matatag-g10-draft",
  "generated_at": "2026-05-22",
  "total_competencies": 153,
  "covered": 4,
  "uncovered": 149,
  "by_subject": {
    "Science": {"total": 30, "covered": 4, "uncovered": 26},
    "Mathematics": {"total": 24, "covered": 0, "uncovered": 24}
  },
  "competencies": {
    "SCI10-PT-I-2": ["types-of-plate-boundaries"],
    "SCI10-PT-I-1": ["introduction-to-plate-tectonics-and-geologic-activities"],
    "EN10LIT-I-2c": []
  }
}
```

### Frontmatter additions

Added to `knowledge/.kebab/education.yaml` `classification_fields`:

```yaml
competency_codes:
  type: list
  description: >
    DepEd MATATAG learning-competency codes this article covers
    (e.g., "SCI10-PT-I-2"). Empty list is valid; lint surfaces them.
curriculum:
  type: str
  default: ""
  description: >
    Name of the curriculum spine this article is tagged against
    (e.g., "matatag-g10-draft"). Matches a file under
    knowledge/.kebab/curriculum/.
```

The `contexts/` agent picks these up automatically — it dynamically
builds Pydantic models from `classification_fields` (see
`app/agents/generate/contexts/__init__.py:29`).

### CLI

Three new commands under a `kebab curriculum` group:

```bash
# Ingest XLSX → spine YAML
kebab curriculum ingest knowledge/raw/curriculum/matatag-learning-objectives-2026-05.xlsx \
  --grade 10 \
  --name matatag-g10-draft

# Build coverage index from curated/ frontmatter
kebab curriculum coverage --name matatag-g10-draft

# Show coverage summary (reads coverage JSON, prints table)
kebab curriculum status --name matatag-g10-draft [--subject Science]
```

`ingest` and `coverage` are sync, side-effect-only-on-disk commands.
`status` is a read-only printer.

### Lint integration

`kebab lint` extended with a new check class `UncoveredCompetencies`
that reads `.coverage.json` and reports LCs with zero articles. Not a
hard failure — same severity as `unanswered_gaps`.

## Workflow

### First-run (this PR's deliverable)

```bash
# 1. Place XLSX in the canonical location
mv ~/Downloads/MATATAG_Learning_Objectives_Database.xlsx \
   knowledge/raw/curriculum/matatag-learning-objectives-2026-05.xlsx

# 2. Build the G10 spine
kebab curriculum ingest \
  knowledge/raw/curriculum/matatag-learning-objectives-2026-05.xlsx \
  --grade 10 --name matatag-g10-draft

# 3. (Out of scope this PR — comes next) tag existing articles
# kebab curriculum tag --domain Knowledge

# 4. Build coverage
kebab curriculum coverage --name matatag-g10-draft

# 5. View status
kebab curriculum status --name matatag-g10-draft
```

### Forward — when new articles are generated

The `contexts/` step (already in `generate`) reads
`classification_fields` from `education.yaml` and now classifies
`competency_codes` and `curriculum` alongside `grade`, `subject`,
etc. No code change needed beyond the YAML — `contexts/` is
spine-aware automatically because of the dynamic model build.

### Refresh — when DepEd updates the XLSX

Re-run `kebab curriculum ingest` with the new file. The spine YAML is
overwritten; the coverage rebuild reads the new spine and re-walks
articles.

## Testing

### Unit tests (`tests/unit/agents/curriculum/`)

- `test_parser.py` — fixture XLSX with 5–10 rows covering edge cases:
  - missing optional columns
  - unicode in competency text (Filipino subjects)
  - rows with NULL/blank grade (filtered out)
  - `--grade 10` filter behavior
- `test_coverage.py` — fixture curated tree with a few articles
  whose frontmatter has `competency_codes`. Verify:
  - reverse index shape
  - per-subject summary counts
  - LCs with empty article list appear as keys

### Integration test (`tests/integration/`)

- End-to-end: ingest a small fixture XLSX → spine YAML → tag a fixture
  article → build coverage → assert the coverage JSON has the article
  under the right LCs.

Coverage budget: ≥90% for new files. No LLM calls, no network — all
sync, all fixture-driven.

## Out of scope (this PR)

- **Backfill of existing articles' competency_codes.** Needs a separate
  LLM-driven `kebab curriculum tag` command. Spec'd in a follow-up.
- **Cross-walking MATATAG ↔ K-12 MELC codes.** When DepEd ships the
  official MATATAG G10 in SY 2027–28, we'll add a mapping table; for
  now, draft codes are the source of truth.
- **Per-competency mastery tracking** (Khan-Academy-style). Different
  product feature; not blocked by this work.
- **Spine-driven article generation.** Letting `organize/` consume the
  spine to propose article topics — useful but separate.
- **Multi-curriculum tagging.** Tagging one article against MATATAG
  AND K-12 MELC simultaneously is supported in the frontmatter
  (`competency_codes` is a list) but not in the tooling yet.

## Open questions (for review)

1. **Spine name convention.** `matatag-g10-draft` is descriptive but
   long. Alternatives: `matatag-g10`, `g10-2026`. I prefer the
   descriptive form so the "draft" caveat is visible until DepEd
   ships the official G10 MATATAG.
2. **Should `kebab curriculum ingest` also write the coverage JSON in
   the same pass?** It would be convenient (one command produces both
   files). The argument against: ingest doesn't read `curated/`,
   coverage does — keep them separate to keep responsibilities clean.
   Going with separate.
3. **CLI naming.** `kebab curriculum` vs `kebab spine`? "Curriculum"
   reads better for the DepEd / education audience; we keep it.
