# KEBAB Pipeline — Developer Guide

This document is for developers contributing to KEBAB. It walks through
each pipeline stage from ingestion through research, explaining what each
stage produces, what it consumes, where its code lives, and how the stages
fit together.

The full pipeline is:

```
ingest → organize → generate → research → research-gaps → research-images → qa → sync → lint
```

This guide covers `ingest` through the three `research-*` agents in
detail. The post-research stages (qa, sync, lint) get a brief reference
section at the end.

## Mental model

KEBAB transforms raw source material into curated, verified markdown
articles indexed in Qdrant. Two invariants hold across every stage:

1. **Markdown is the source of truth.** The Qdrant index is a derived
   read-only view; deleting the collection and re-running `kebab sync`
   reproduces it from the curated tree.
2. **No source, no save.** Content without a traceable source is
   discarded at every stage. The `Source` model is mandatory in
   frontmatter; the verifier refuses to write findings that lack a URL
   or local source path.

Every stage is **idempotent and resumable** — running it twice produces
the same result. This is what lets the operator (or a future supervisor
agent) re-run any stage without worrying about double-writes.

## Directory layout for stage code

```
app/
  agents/
    ingest/         # PDF/web/CSV adapters and the registry
    organize/       # Hierarchy planner; produces plan.json + stub markdown
    generate/       # Contexts → gaps → write_articles in one orchestrator
    research/       # Claim verification (planner + verifier + writer)
    research_gaps/  # Standalone gap answering
    research_images/# Standalone Wikipedia image enrichment
    qa/             # Q&A pair enrichment + gap discovery
    lint/           # Health checks (no LLM)
    sync/           # Embed + upsert to Qdrant
  core/
    research/       # Shared adapter dispatch (no LLM)
    images/         # Multimodal describer + figure manifest
    sources/        # Adapter protocol, source index, fetcher
    markdown.py     # Read/write articles; section/footnote helpers
    store.py        # Qdrant wrapper
    llm/            # Model resolution, embeddings, token counting
```

Every agent directory follows the same shape: the main file is named
after the folder (`research/research.py`, `organize/organize.py`,
`generate/generate.py`) and exposes a `run()` function. Other files in
the directory are helpers — planners, classifiers, writers — each doing
one job.

## Knowledge directory layout

The knowledge base lives outside the source tree, under
`settings.KNOWLEDGE_DIR` (typically `./knowledge/`).

```
knowledge/
  raw/
    documents/        # Original PDFs, HTML, datasets — never modified after ingest
    inbox/            # Research-fetched artifacts pending review
  processed/
    documents/<stem>/
      text.md         # Per-page text with [Figure p.N: ...] markers
      figures.json    # One record per extracted figure (described or filtered)
      figures/        # Raw image bytes
  curated/
    <Domain>/<Subdomain>/<Topic>/<article>.md
    <article>/figures/<slug>/  # Article-local copies of figures used in the body
  .kebab/
    sources.json      # Source index — id ↔ raw_path ↔ checksum mapping
    plan.json         # Per-domain hierarchy plan from organize
    image_skip_keywords.txt  # Decorative-image prefilter for research-images
    .qdrant/          # Local Qdrant data files
```

The naming convention `raw/` → `processed/` → `curated/` is a strict
ratchet: stages only ever consume from earlier directories and produce
into later ones. No stage writes to a directory it also reads from.

---

## Stage 1: Ingest

**Code:** `app/agents/ingest/`
**CLI:** `kebab ingest pdf|web|retry-errors`
**Inputs:** raw files on disk (PDF, web URL)
**Outputs:** `raw/documents/`, `processed/documents/<stem>/`, `.kebab/sources.json`

### What it does

Each adapter implements the `SourceAdapter` protocol (defined in
`app/core/sources/adapter.py`):

```python
class SourceAdapter(Protocol):
    name: ClassVar[str]
    default_tier: SourceTier

    def discover(self, query: str, *, limit: int = 10) -> list[Candidate]: ...
    def fetch(self, candidate: Candidate) -> FetchedArtifact: ...
```

`discover` is cheap and idempotent (returns metadata, not bytes).
`fetch` writes bytes to disk under `raw/` and returns a `FetchedArtifact`
with provenance populated. The two-step split is what lets the
research agent call adapters as tools without needing human approval.

The PDF adapter (`agents/ingest/pdf.py`) is the heaviest:

1. Copies the PDF byte-for-byte to `raw/documents/<basename>.pdf`.
2. Extracts text with PyMuPDF, page by page, into `processed/documents/<stem>/text.md`.
3. Extracts every figure to `processed/documents/<stem>/figures/`.
4. Runs deterministic filters (`core/images/filter_images.py`) to drop
   tiny / repeated / duplicated images.
5. Calls `core/images/image_describer.py` (multimodal Gemini) on the
   survivors. The describer returns either a 1–3 sentence caption or
   the literal sentinel `"DECORATIVE"`.
6. Writes `processed/documents/<stem>/figures.json` — one record per
   extracted figure, including describer output, filter decisions, and
   any errors.
7. Registers the source in `.kebab/sources.json` with a unique integer ID,
   the SHA256 of the raw bytes, and the adapter name.

The web adapter (`agents/ingest/web.py`) calls Jina Reader to convert a
URL to plaintext markdown, then runs the same source-index registration.

### Key invariants

- **Idempotent**: re-running on the same PDF detects the existing
  `text.md` and skips. Use `--force` after changing filter thresholds
  or the describer prompt.
- **Failure-tolerant**: if a single figure fails to describe, the
  ingest still completes; failed figures get an `ERROR:` description.
  Re-run with `kebab ingest retry-errors --stem <stem>` to retry only
  the failed ones.
- **Source IDs are stable**: once assigned, an integer ID never changes.
  Curated articles reference sources by these IDs in their frontmatter.

### Models used

- `FIGURE_MODEL` — multimodal LLM for figure descriptions (default `gemini-flash-lite`).

### Data shapes

```python
# app/core/sources/adapter.py
class FetchedArtifact(BaseModel):
    raw_path: Path        # where the bytes landed under raw/
    source: Source        # populated provenance envelope
    content_hash: str     # SHA256 of the raw bytes
    license: str | None

# app/core/sources/index.py
class SourceEntry(BaseModel):
    id: int               # stable, unique
    stem: str             # processed/documents/<stem>/
    raw_path: str         # relative to knowledge/
    title: str
    tier: SourceTier
    checksum: str
    adapter: str
```

---

## Stage 2: Organize

**Code:** `app/agents/organize/`
**CLI:** `kebab organize --domain <name> [--force]`
**Inputs:** `processed/documents/<stem>/text.md` (every ingested source)
**Outputs:** `.kebab/<domain>-plan.json`, stub markdown files in `curated/`

### What it does

`organize/organize.py::run()` is the entry point. It has three code paths:

1. **No cache or `--force`**: build a manifest of every processed source,
   call the LLM proposer (`organize/agent.py::propose_hierarchy`) to
   produce a `HierarchyPlan` (domain → subdomain → topic → article), and
   materialize stub markdown files at the planned paths.
2. **Cache + new sources**: call `propose_incremental_hierarchy` with
   only the *new* sources, then merge the new plan into the existing one
   via `organize/merge.py::_merge_plans`.
3. **Cache + no new sources**: load and return the cached plan.

The plan is persisted to `knowledge/.kebab/<domain>-plan.json`. The
stub markdown files are empty articles with frontmatter only — they
reserve the curated paths so subsequent stages know where to write.

### Key invariants

- **Plan paths are reserved**: every article in the plan has a
  `md_path` field; later stages always write to that exact path. There
  are no parallel trees.
- **Incremental merging**: the merge step preserves human edits to the
  plan. Re-running with new sources extends the plan instead of replacing
  it.
- **Domain-keyed**: each domain has its own plan file. A repo can have
  multiple plans (e.g. `science-plan.json`, `legal-plan.json`).

### Models used

- `ORGANIZE_MODEL` (default `gemini-flash`).

### Data shapes

```python
# app/agents/organize/agent.py
class HierarchyPlan(BaseModel):
    nodes: list[PlanNode]

class PlanNode(BaseModel):
    id: str               # e.g. "SCI-ESC-003"
    name: str
    level_type: Literal["domain", "subdomain", "topic", "article"]
    parent_id: str | None
    md_path: str | None   # absolute path; only set on article nodes
    source_files: list[str]
    description: str
```

---

## Stage 3: Generate

**Code:** `app/agents/generate/`
**CLI:** `kebab generate [--domain <name>] [--force]`
**Inputs:** `.kebab/<domain>-plan.json`, `processed/documents/<stem>/text.md`
**Outputs:** populated curated `.md` files

### What it does

`generate/generate.py::run()` is one orchestrator that runs three steps
in sequence:

1. **Contexts** (`agents/generate/contexts/`) — for each existing article,
   the LLM picks the right vertical metadata (education / healthcare /
   legal / policy) and writes it under `frontmatter.contexts.<vertical>`.
2. **Gaps** (`agents/generate/gaps.py`) — diffs the plan against the
   curated tree to find articles that are still stubs (`reason="new"`)
   or whose source set has changed (`reason="stale"`).
3. **Writer** (`agents/generate/writer.py`) — for each gap, loads the
   relevant processed source text plus the figure manifest, calls the
   LLM to write a grounded article body with footnotes (`[^N]`) and
   figure markers (`[FIGURE:N]`), then post-processes:
   - Resolves footnote markers to source URLs.
   - Validates each `[FIGURE:N]` marker against the manifest and
     replaces it with `![desc](figures/<slug>/<id>.ext)` markdown.
   - Copies the used figure files into the article-local
     `figures/<slug>/` directory.
   - Computes a `summary` field for the frontmatter.

The post-processing is all in `agents/generate/writer.py` and
`core/images/figures.py`. After this step, the curated markdown is
self-contained: every footnote resolves, every image renders.

### Key invariants

- **Contexts run first.** The writer needs the education context (grade
  level, subject) to scale prose complexity, so contexts must already be
  populated before write_articles is called. The orchestrator enforces
  this order.
- **Figure manifest is the contract.** The writer can only reference
  figures that appear in the manifest; invalid `[FIGURE:N]` markers are
  stripped with a warning rather than left in the body.
- **Source IDs flow through.** Each generated article's frontmatter
  `sources` list cites only IDs that exist in `.kebab/sources.json`. The
  writer is given the local manifest of source IDs available for that
  article.
- **Idempotent**: an article that's already populated is skipped unless
  `--force` is passed.

### Models used

- `CONTEXTS_MODEL` (default `gemini-flash`)
- `GENERATE_MODEL` (default `gemini-flash`)

### Data shapes

```python
# app/agents/generate/gaps.py
class Gap(BaseModel):
    id: str               # PlanNode.id
    name: str
    description: str
    source_files: list[str]
    target_path: str | None  # the curated md path reserved by organize
    reason: Literal["new", "stale"]

class GapReport(BaseModel):
    gaps: list[Gap]
    existing: list[Gap]   # articles that didn't need regeneration

# app/agents/generate/generate.py
class GenerateStageResult:
    contexts_updated: int
    gaps_found: int
    articles_written: int
    articles_skipped: int
```

After this stage, every article in the plan is either populated or
skipped, and confidence is at level 1 (has sources, not yet researched).

---

## Stage 4: Research (claim verification)

**Code:** `app/agents/research/`
**CLI:** `kebab agent research [<id>] [--all] [--budget 10]`
**Inputs:** a curated article, the source index, search adapters
**Outputs:** the same article with confirmations / appends / disputes
applied to the body, plus updated frontmatter metadata

### Architecture

Four files, one job each:

| File | Role | LLM? |
|------|------|------|
| `research.py` | Orchestrator: load → plan → search → verify → write back | No |
| `planner.py` | Extract claims and generate search queries | Yes |
| `verifier.py` | `classify_finding` and `judge_dispute` agents | Yes |
| `writer.py` | Apply confirmed/appended/disputed findings to the body | No |

The shared adapter dispatch (`core/research/searcher.py`) is in core,
not in the agent — it's reused by `research_gaps` too.

### Flow

```
1. find_article_by_id(settings.CURATED_DIR, article_id)
2. plan = planner.plan_research(settings, PlannerDeps(article_name, body, ...))
   → ResearchPlan(claims=[ClaimEntry], queries=[SearchQuery])
3. for each query in plan.queries (until budget):
       sources = core.research.searcher.search(settings, adapter, query, limit=2)
       for src in sources:
           for claim_idx in query.target_claims:
               result = verifier.classify_finding(settings, claim, src.title, src.content)
               if result.outcome == "dispute":
                   if not verifier.judge_dispute(...).is_genuine:
                       skip
               findings.append((claim, result, src.title, src.url))
4. new_body = writer.apply_findings_to_article(body, findings)
5. Update frontmatter:
       fm.research_claims_total = len(plan.claims)
       fm.external_confirms = count_external_footnotes(new_body)
       fm.dispute_count = extract_disputes(new_body)
       fm.researched_at = today
6. write_article(path, fm, new_body)
```

### Outcomes

The verifier classifies each (claim, source) pair into one of three
outcomes:

- **`confirm`** — source agrees. The writer appends a footnote citation
  to the claim's existing sentence: `Plates move...[^7]`.
- **`append`** — source has new related information. The writer adds a
  new sentence at the end of the claim's section, marked
  `<!-- appended -->` with a footnote.
- **`dispute`** — source contradicts the claim. The result goes through
  a second LLM (`judge_dispute`) which strips out phrasing/scope
  differences. Genuine contradictions land in `## Disputes` with both
  the claim and the contradicting passage.

### Key invariants

- **No sources adapter is hard-coded.** The orchestrator computes
  `available_adapters = ["wikipedia"] + (["tavily"] if TAVILY_API_KEY)`
  and hands the list to the planner.
- **Cross-agent imports forbidden.** `research/` does not import from
  `research_gaps/` or `research_images/` and vice versa. They share
  only `core/research/searcher.py`.
- **No callable swap-points on `run()`.** Tests inject at the per-step
  layer instead — `planner.plan_research(..., agent=stub)`,
  `monkeypatch.setattr(research, "search", stub)`. The old
  `planner=, searcher=, classifier=` parameters are gone.
- **Footnote dedup**: the writer reuses existing footnote numbers when
  the same URL is cited twice, instead of creating duplicates.

### Models used

- `RESEARCH_PLANNER_MODEL` — for the planner agent
- `RESEARCH_EXECUTOR_MODEL` — for `classify_finding`
- `RESEARCH_JUDGE_MODEL` — for `judge_dispute`

### Data shapes

```python
# app/agents/research/planner.py
class ClaimEntry(BaseModel):
    text: str
    section: str          # markdown heading
    paragraph: int

class SearchQuery(BaseModel):
    query: str
    adapter: str          # "wikipedia" | "tavily"
    target_claims: list[int]   # indices into ResearchPlan.claims

class ResearchPlan(BaseModel):
    claims: list[ClaimEntry]
    queries: list[SearchQuery]

# app/agents/research/verifier.py
class FindingResult(BaseModel):
    outcome: Literal["confirm", "append", "dispute"]
    reasoning: str
    evidence_quote: str
    new_sentence: str | None      # set on "append"
    contradiction: str | None     # set on "dispute"

# Public type alias for what the writer consumes
FindingTuple = tuple[ClaimEntry, FindingResult, str, str]

# app/agents/research/research.py
class ResearchResult(BaseModel):
    article_id: str
    claims_total: int
    confirms: int
    appends: int
    disputes: int
    findings: list[str]   # human-readable summaries
```

### Frontmatter written

```yaml
research_claims_total: 23
external_confirms: 20
dispute_count: 0
researched_at: '2026-04-12'
```

---

## Stage 5: Research-gaps

**Code:** `app/agents/research_gaps/`
**CLI:** `kebab agent research-gaps [<id>] [--all] [--budget 5]`
**Inputs:** a curated article with a `## Research Gaps` section
**Outputs:** the same article with answered gaps rewritten as Q/A
blocks; updated frontmatter metadata

### Architecture

Four files, deliberately mirroring `research/`'s shape but with
different semantics (gap questions are not claims):

| File | Role | LLM? |
|------|------|------|
| `research_gaps.py` | Orchestrator: load → query → search → classify → write back | No |
| `query_planner.py` | Generate search queries from gap questions | Yes |
| `classifier.py` | Decide whether a source answers a question | Yes |
| `writer.py` | Rewrite gap lines in-place as Q/A blocks | No |

### Flow

```
1. find_article_by_id → load fm, body
2. gaps = [g for g in extract_research_gaps(body) if not g.startswith("**Q:")]
   (i.e. unanswered questions only — answered ones are already Q/A blocks)
3. plan = query_planner.plan_queries(settings, QueryPlannerDeps(gap_questions=gaps, ...))
   → GapQueryPlan(queries=[GapQuery(query, adapter, target_gap_idx)])
4. for each query in plan.queries (until budget):
       skip if target_gap_idx is already answered
       sources = core.research.searcher.search(...)
       for src in sources:
           result = classifier.answer_question(settings, question=gaps[idx], ...)
           if result.is_answered:
               answers.append(GapAnswer(gap_idx, result.answer, src.title, src.url))
               break  # one answer per gap
5. new_body = writer.apply_answers_to_gaps(body, gaps, answers)
6. fm.gaps_answered = len(answers); fm.gaps_researched_at = today
7. write_article(path, fm, new_body)
```

### Why this is a separate agent

The original research agent had a `mode="all"|"content"|"gaps"` flag
that branched the orchestrator's behavior. That's gone now: gap-answering
is a standalone agent because:

1. The "items" being researched are different (open questions vs
   existing claims), so the planner needs no claim-extraction step.
2. The classifier needs different semantics (yes/no answer, not
   confirm/append/dispute), so it has a smaller dedicated prompt.
3. The supervisor agent (future) needs to invoke gap-answering without
   re-verifying the whole article first.

### Key invariants

- **Reuses `core/research/searcher.py`** for adapter dispatch — the
  same shared module the verification agent uses.
- **Owns its own classifier** rather than reusing the verifier — the
  semantics are different. This is the layering principle: pure plumbing
  is shared via core/, but semantics stay agent-local.
- **Idempotent**: already-answered gaps (lines starting with `**Q:`)
  are skipped.

### Models used

- `RESEARCH_PLANNER_MODEL` — for `plan_queries`
- `RESEARCH_EXECUTOR_MODEL` — for `answer_question`

### Data shapes

```python
# app/agents/research_gaps/query_planner.py
class GapQuery(BaseModel):
    query: str
    adapter: str
    target_gap_idx: int   # index into the gap_questions list

class GapQueryPlan(BaseModel):
    queries: list[GapQuery]

# app/agents/research_gaps/classifier.py
class GapClassification(BaseModel):
    is_answered: bool
    answer: str           # 1–2 sentences; empty when not answered
    reasoning: str

# app/agents/research_gaps/writer.py
@dataclass
class GapAnswer:
    gap_idx: int
    answer_text: str
    source_title: str
    source_url: str
```

### Frontmatter written

```yaml
gaps_answered: 3
gaps_researched_at: '2026-04-12'
```

---

## Stage 6: Research-images

**Code:** `app/agents/research_images/`
**CLI:** `kebab agent research-images [<id>] [--all]`
**Inputs:** a curated article with existing Wikipedia footnotes
**Outputs:** the same article with `![desc](path)` image refs appended;
downloaded image files under `figures/<slug>/`

### Architecture

Five files, each one job:

| File | Role | LLM? |
|------|------|------|
| `research_images.py` | Orchestrator: targets → fetch → describe → write | No |
| `targets.py` | Regex over body footnotes for Wikipedia URLs | No |
| `fetcher.py` | Wikipedia images API + httpx download + skip-keyword filter | No |
| `describer.py` | Wraps `core/images/image_describer.describe_image` | Yes |
| `writer.py` | Append `![desc](path)` markdown to the body | No |

### Flow

```
1. find_article_by_id → load fm, body
2. targets = targets.extract_wikipedia_targets(body)
   (regex finds [^N]: [Title](https://en.wikipedia.org/wiki/...) footnotes)
3. for each target (deduped by title):
       images = fetcher.fetch_wikipedia_images(target.title, limit=3)
       for img in images[:2]:
           if fetcher.is_decorative_by_keyword(img, skip_keywords): skip
           local_path = fetcher.download(img, dest=figures_dir)
           if local_path: candidates.append(ImageCandidate(...))
4. for each candidate:
       desc = describer.describe(settings, candidate)
       if desc == "DECORATIVE":
           candidate.local_path.unlink(missing_ok=True)
           continue
       candidate.llm_description = desc
       approved.append(candidate)
5. new_body = writer.append_figure_refs(body, approved, article_slug=path.stem)
6. fm.images_added = len(approved); fm.images_researched_at = today
7. write_article(path, fm, new_body)
```

### Ordering constraint

`research-images` requires `research` (Stage 4) to have run on the
article at least once. The reason: image targets come from existing
Wikipedia footnotes, which the verification stage adds. If no Wikipedia
footnotes are present, the orchestrator logs a warning and returns an
empty `ImagesResult`. The dependency is documented but not enforced via
state machinery — the operator (or supervisor agent) is responsible for
the order.

### Skip-keyword prefilter

`fetcher.load_skip_keywords` reads
`.kebab/image_skip_keywords.txt` (one keyword per line) and uses it to
drop decorative images by their Wikipedia description before downloading.
This is a deterministic pre-filter that runs before the LLM describer,
saving on cost and quota.

### Key invariants

- **Standalone agent.** Like `research_gaps`, this is invokable
  independently and shares only `core/`. It does not import from
  `research/` or `research_gaps/`.
- **Decoratives are deleted from disk.** The describer's `"DECORATIVE"`
  sentinel is treated as "delete this download" so the figures
  directory doesn't accumulate noise.
- **No figure-numbering collision** with `[FIGURE:N]` from generate —
  the generate stage's markers are temporary placeholders that get
  resolved to plain `![desc](path)` markdown before the body is saved,
  so there's no shared numbering space.

### Models used

- `FIGURE_MODEL` — for image description (same model the PDF ingest stage uses)

### Data shapes

```python
# app/agents/research_images/targets.py
@dataclass(frozen=True)
class WikiTarget:
    title: str   # decoded from URL path
    url: str

# app/agents/research_images/fetcher.py
@dataclass
class ImageCandidate:
    local_path: Path
    source_title: str       # the Wikipedia article title
    raw_description: str    # description from Wikipedia API
    llm_description: str    # filled in by the describer
```

### Frontmatter written

```yaml
images_added: 4
images_researched_at: '2026-04-12'
```

---

## Confidence model

The confidence level of an article is computed at sync time
(`core/confidence.py`) from the frontmatter the research stages produce:

| Level | Meaning |
|-------|---------|
| 0 | No sources |
| 1 | Has sources, not yet researched (`researched_at` missing) |
| 2 | Researched, but `<70%` of claims confirmed OR has disputes |
| 3 | Researched, `≥70%` confirmed, 0 disputes — **production gate** |
| 4 | Human verified |

Consumers should only trust articles at level 3+. Healthcare verticals
may require level 4.

The lint stage (`app/agents/lint/agent.py`) flags articles below the
confidence gate, articles with stale `researched_at` (>180 days), and
articles with unanswered research gaps.

---

## Post-research stages (brief reference)

### Q&A (`agents/qa/`)

- Runs after generate. Reads the article body and adds Q/A pairs to
  the `## Q&A` section. Question depth scales with the education
  context (grade level).
- Also discovers knowledge gaps and writes them to `## Research Gaps`.
  These gaps are what `research-gaps` later answers.
- Model: `QA_MODEL` (default `gemini-flash`).

### Sync (`agents/sync/`)

- Reads every curated article, computes confidence, embeds the body,
  and upserts to Qdrant via `core/store.py`.
- Idempotent — articles whose checksum hasn't changed are skipped.
- Model: `EMBEDDING_MODEL` (default `gemini-embedding-001`).

### Lint (`agents/lint/`)

- Pure-Python health checks; no LLM. Surfaces:
  `missing_sources`, `oversized` (>50k tokens), `broken_prerequisite`,
  `stale_verification`, `orphan`, `below_confidence_gate`,
  `unanswered_gaps`.

---

## Typical workflows

### First run

```bash
kebab ingest pdf --input knowledge/raw/documents/
kebab organize --domain Science --force
kebab generate --domain Science
kebab agent research --all
kebab agent qa --once
kebab agent research-gaps --all
kebab agent research-images --all
kebab sync
kebab agent lint
```

### Incremental (new sources added)

```bash
kebab ingest pdf --input new-file.pdf
kebab organize --domain Science     # extends existing plan
kebab generate --domain Science     # writes new articles, contexts, gaps
kebab agent research --all
kebab agent research-gaps --all
kebab agent research-images --all
kebab sync
```

The order of the three `research-*` commands matters: `research-images`
needs `research` to have populated Wikipedia footnotes first, but
`research-gaps` can run before or after `research-images`.

---

## Configuration

All settings in `.env` (config) and `.env.local` (secrets), loaded by
`app/config/config.py`. Per-operation models are independent so you can
mix providers:

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

Model aliases are defined in `app/config/models.yaml` and resolved by
`app/core/llm/resolve.py::resolve_model`. Available aliases:
`gemini-flash`, `gemini-flash-lite`, `gemini-pro` (Google);
`gpt-5.4-mini` (Azure OpenAI); `haiku-4.5`, `sonnet-4.6` (AWS Bedrock /
Claude); `minimax` (MiniMax). The full list lives in `models.yaml`.

---

## Article anatomy

A fully-processed article looks like this:

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
summary: Three types of plate boundaries — convergent, divergent, transform — and how each shapes Earth's crust.
research_claims_total: 23
external_confirms: 20
dispute_count: 0
researched_at: '2026-04-12'
gaps_answered: 2
gaps_researched_at: '2026-04-12'
images_added: 3
images_researched_at: '2026-04-12'
---

# Types of Plate Boundaries

Article body with inline figures and footnotes[^1][^2].

![Diagram of plate boundaries](figures/types-of-plate-boundaries/p005_f02.jpeg)

![Map of Earth's tectonic plates](figures/types-of-plate-boundaries/wiki-tectonic-map.svg)

## Q&A

**Q: What are the three types of plate boundaries?**
Divergent, convergent, and transform, each defined by relative plate motion.

## Research Gaps

- **Q: How does slab pull compare to convection as a driving force?**
  **A:** Slab pull is now considered the dominant mechanism. (Source: [Slab pull](https://en.wikipedia.org/wiki/Slab_pull))
- What is the role of paleomagnetism in plate tectonics evidence?

## Disputes

- **Claim**: "Neither new crust is created nor old crust destroyed."
  **Section**: Transform Plate Boundaries, paragraph 1
  **External source**: [Plate tectonics](https://en.wikipedia.org/wiki/Plate%20tectonics)
  **Contradiction**: Source indicates some crust can be created/destroyed at leaky transforms.

[^1]: [1] [SCI10 Q1 M2 Plate Boundaries](../../../raw/documents/grade_10/science/SCI10_Q1_M2_Plate%20Boundaries.pdf)
[^2]: [Plate tectonics](https://en.wikipedia.org/wiki/Plate%20tectonics)
```

Each section is owned by a specific stage:

| Section | Owner stage |
|---------|-------------|
| `frontmatter.sources` | ingest + organize (id assignment) |
| `frontmatter.contexts` | generate (contexts step) |
| `frontmatter.summary` | generate (writer step) |
| `frontmatter.research_*` | research |
| `frontmatter.gaps_*` | research-gaps |
| `frontmatter.images_*` | research-images |
| Body prose + figures | generate (writer) |
| Inline footnotes (`[^1]`) — local sources | generate |
| Inline footnotes (`[^2]+`) — external | research |
| Body image refs from Wikipedia | research-images |
| `## Q&A` section | qa |
| `## Research Gaps` (open questions) | qa |
| `## Research Gaps` (answered Q/A) | research-gaps |
| `## Disputes` | research |

This split is the contract that lets the supervisor agent (future)
re-run any single stage without stepping on another stage's output.
