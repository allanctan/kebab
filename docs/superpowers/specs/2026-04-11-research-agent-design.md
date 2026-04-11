# Research Agent Design (M21)

Autonomous research agent that enriches and verifies articles against independent external sources. Replaces the current verify stage.

## Problem

The current verify stage asks two LLMs whether the article body is grounded in its cited sources. This is circular — the article was generated from those same sources. Real verification requires checking claims against **independent external sources**.

## Design

### Core concept

One agent, two modes, three outcomes.

**Modes:**
- **Discovery** — find sources for articles that need writing (gaps)
- **Enrichment + Verification** — read an existing article, search external sources, and produce three types of output per finding

**Three outcomes per finding:**
- **Confirm** — external source supports an existing claim. Add a footnote citation to that sentence.
- **Append** — external source has new relevant information. Add a new sentence with its source citation.
- **Dispute** — external source contradicts a claim. Append to `## Disputes` section in the article body.

### Two-stage architecture

#### Stage 1: Planner

A pydantic-ai agent that reads the article and produces a research plan.

**Input:**
- Article body + frontmatter
- Available adapters (tavily, wikipedia, openstax)
- Budget constraint

**Output (structured):**
- List of extracted claims (text + section + paragraph location)
- List of search queries, each targeting a specific adapter
- Reasoning for adapter selection (e.g. "educational content → try OpenStax first")

The planner decides adapter strategy based on the topic. For educational content, prefer OpenStax (tier 2) and Wikipedia (tier 4). For broader topics, include Tavily.

The plan auto-executes — no human review of the plan itself.

#### Stage 2: Executor

Runs the research plan step by step.

**For each search query:**
1. Call `adapter.discover(query)` to get candidates
2. Call `adapter.fetch(candidate)` for the top candidates
3. Read the fetched content
4. Compare against the article's claims
5. Classify each finding as confirm, append, or dispute

**Output:**
- Modified article body with new footnotes and appended sentences
- `## Disputes` section (if any contradictions found)
- Fetched sources landed in `raw/inbox/` for provenance

### Article modification

**Confirm:** When an external source supports an existing claim, add a footnote to that sentence. The footnote continues numbering from where the generate stage left off. External footnotes are distinguished by linking to URLs (not local PDF paths).

```markdown
The Earth's lithosphere is divided into tectonic plates[^1][^4].

[^1]: [3] [SCI10 Q1 M2 Plate Boundaries](../../../raw/documents/grade_10/science/SCI10_Q1_M2_Plate%20Boundaries.pdf)
[^4]: [Wikipedia: Plate tectonics](https://en.wikipedia.org/wiki/Plate_tectonics)
```

**Append:** New relevant information is added as a new sentence at the end of the relevant section, with its footnote.

```markdown
The three types are divergent, convergent, and transform[^1]. Recent studies have also identified
diffuse plate boundaries where deformation is spread across a broad zone[^5].

[^5]: [OpenStax: Geology](https://openstax.org/books/geology/pages/...)
```

**Dispute:** Contradictions go to a dedicated section appended to the article body.

```markdown
## Disputes

- **Claim**: "Convection currents are the sole driver of plate movement"
  **Section**: § Causes of Plate Movement, paragraph 2
  **External source**: [Wikipedia: Mantle convection](https://en.wikipedia.org/wiki/Mantle_convection)
  **Contradiction**: Source states that slab pull, not convection, is now considered the dominant driving force.
```

### Confidence computation

Confidence is derived from the article body — no new frontmatter fields.

**Counting method:**
- **Confirm count** — footnotes pointing to external URLs (http/https). Distinguished from original source footnotes which link to local PDF paths.
- **Dispute count** — entries in the `## Disputes` section.
- **Total claims** — extracted during the planning phase. Stored in frontmatter as `research_claims_total: int` so confidence computation can calculate the percentage without re-parsing the body. Also store `researched_at: date` to track when research was last run.

**Levels:**

| Level | Criteria |
|---|---|
| 0 | No sources |
| 1 | Has sources, research not yet run |
| 2 | Research ran, < 70% claims confirmed OR has disputes |
| 3 | Research ran, >= 70% claims confirmed, 0 disputes |
| 4 | Human verified |

Disputes always cap confidence at 2 regardless of confirm count.

### CLI

```bash
# Enrichment + verification (primary use)
kebab agent research <article-id>
kebab agent research --all

# Discovery mode (for gaps)
kebab agent research --discover <gap-id>
kebab agent research --discover --all-gaps

# Options
--budget 0.10          # per-article budget in USD
--adapters wikipedia,openstax   # restrict adapter selection
```

### Cost control

- `GATHER_BUDGET_USD_PER_DAY` setting enforced across all research runs
- Per-article budget via `--budget` flag (default from settings)
- Logfire instrumentation on all adapter calls for cost tracking

### Adapter strategy

The planner selects adapters based on context:

| Content type | Primary adapter | Secondary |
|---|---|---|
| K-12 education | OpenStax (tier 2) | Wikipedia |
| General science | Wikipedia | Tavily |
| Current events | Tavily | Wikipedia |
| Any topic | All three if budget allows | — |

### Provenance

Fetched external sources land in `raw/inbox/` with full provenance sidecars (adapter, checksum, retrieved_at, license). They are also registered in the source index with their metadata.

The `raw/inbox/` staging area preserves the "no source, no save" invariant — every citation in the article body traces to a fetchable source.

### Replaces verify stage

The current `kebab verify` command and `app/pipeline/verify.py` are replaced by `kebab agent research`. The verify stage's multi-LLM same-source checking is superseded by single-agent independent-source checking.

`compute_confidence` is updated to use the new counting method (external footnotes + disputes) instead of `VerificationRecord` counts.

### Files

| File | Change |
|---|---|
| `app/agents/research/agent.py` (new) | Research agent: planner + executor |
| `app/agents/research/prompts/planner.md` (new) | Planner system prompt |
| `app/agents/research/prompts/executor.md` (new) | Executor system prompt |
| `app/core/confidence.py` | Update `compute_confidence` for new model |
| `app/pipeline/verify.py` | Deprecate — replaced by research agent |
| `app/cli.py` | Add `kebab agent research` commands |
| `app/pipeline/ingest/inbox.py` (new) | `raw/inbox/` staging helpers |

### Not changed

- Adapter protocol (`SourceAdapter`) — research agent uses existing adapters as-is
- Source index — external sources registered same as ingested sources
- Generate stage — untouched
- Frontmatter schema — no new fields (confidence derived from body)

### Edge cases

- **No external sources found**: research ran but found nothing. Confidence stays at 1 (not enough coverage to confirm).
- **Adapter API down**: skip that adapter, note in output. Don't fail the whole run.
- **Budget exhausted mid-article**: stop searching, process what we have. Partial confirmation is better than none.
- **Article already researched**: re-running appends new findings. Disputes section is cumulative (lint cleans up resolved disputes later).
