# Batch Verifier — Design Spec

**Date:** 2026-04-18
**Status:** Draft
**Branch:** `feature/content-expert`

## Summary

Replace the per-claim, per-source classification loop in the research
agent with a single LLM call that verifies all claims against all
fetched sources at once. This reduces ~24 sequential LLM calls to 1,
cutting research time from minutes to seconds.

Also: expand the education vertical's authoritative sources from 14 to
100 domains, improving hit rate for claim verification.

## Problem

The current research verification loop is:

```
for each search query:
    fetch sources (1-2 per query)
    for each source:
        for each target claim:
            classify_finding()    ← 1 LLM call
            if dispute:
                judge_dispute()   ← 1 more LLM call
```

For 12 claims × 2 sources = ~24 LLM calls + ~2 judge calls, all
sequential. Each call takes 2-5 seconds → 1-2 minutes just for
classification.

## Solution

### New pipeline

```
planner (1 LLM call) → fetch sources (N network calls) → batch_verify (1 LLM call) → writer
```

Total LLM calls for research: **2** (planner + batch verifier).

### Architecture

**New file:** `app/agents/research/batch_verifier.py`

Contains:
- `BatchFinding` model — one finding per claim
- `BatchVerifierDeps` dataclass — deps for the agent
- `batch_verify()` function — runs the pydantic-ai agent

**Modified file:** `app/agents/research/research.py`

The `run()` function's inner loop (lines ~270-330) is replaced with a
single call to `batch_verify()`. The fetch loop (discovering + fetching
sources per query) remains — only the classification changes.

**Preserved files:**
- `planner.py` — unchanged
- `writer.py` — unchanged (still consumes `FindingTuple`)
- `verifier.py` — kept as importable module but no longer called by
  `research.py`. Both CLI (`kebab research`) and editorial use the
  batch path. The old per-pair functions remain available for any
  future agent that needs single-claim verification.

### BatchFinding model

```python
class BatchFinding(BaseModel):
    claim_idx: int
    outcome: Literal["confirm", "append", "dispute", "unverified"]
    source_title: str
    source_url: str
    evidence_quote: str
    new_sentence: str | None       # for append
    contradiction: str | None      # for dispute
    dispute_category: DisputeCategory | None  # for dispute (5-category taxonomy)
    reasoning: str
```

New outcome `"unverified"` — the source content didn't address this
claim. Currently implicit (no finding generated); now explicit so the
orchestrator knows which claims weren't covered.

### BatchVerifierDeps

```python
@dataclass
class BatchVerifierDeps:
    settings: Settings
    article_name: str
    article_body: str
    claims: list[ClaimEntry]
    sources: list[SourceContent]
    authoritative_domains: list[str]
```

### batch_verify() function

```python
def batch_verify(
    settings: Settings,
    deps: BatchVerifierDeps,
    *,
    agent: Agent[BatchVerifierDeps, BatchVerifyResult] | None = None,
) -> list[BatchFinding]:
```

The agent receives one prompt containing:
1. Article body
2. Numbered claims list
3. All source content (each truncated to 4K tokens)
4. Authoritative domains for trust context
5. The 5-category dispute taxonomy with instructions to only surface
   categories 1-3 (factual_error, misleading_simplification,
   contested_or_opinion)

Returns one `BatchFinding` per claim. The LLM picks the most relevant
source for each claim and classifies in one pass.

### BatchVerifyResult

```python
class BatchVerifyResult(BaseModel):
    findings: list[BatchFinding]
```

### Prompt design (`prompts/batch_verifier.md`)

```
# Research Batch Verifier

You verify an article's claims against a set of external sources.
For EVERY claim listed below, produce exactly one BatchFinding.

## Rules

1. For each claim, find the most relevant source. If multiple sources
   address the same claim, prefer the one from an authoritative domain.
2. Outcomes:
   - "confirm" — a source agrees with the claim. Cite the evidence.
   - "append" — a source has new related information not in the claim.
     Provide a new_sentence to add.
   - "dispute" — a source contradicts the claim. Classify the dispute:
     - factual_error: claim is factually wrong
     - misleading_simplification: claim oversimplifies to the point of
       being misleading
     - contested_or_opinion: claim presents one side of a debated topic
     - acceptable_simplification: simplified but not misleading (DO NOT
       surface — mark as confirm instead)
     - false_positive: source and claim are talking about different
       things (DO NOT surface — mark as unverified instead)
   - "unverified" — no source addresses this claim.
3. Every claim MUST get exactly one finding. Do not skip claims.
4. evidence_quote must be a direct passage from the source, not
   paraphrased.
```

### Integration with research.py

The `run()` function changes from:

```python
# OLD: per-pair loop
for sq in plan.queries:
    sources = search(...)
    for src in sources:
        for claim_idx in sq.target_claims:
            result = classify_finding(...)
            if result.outcome == "dispute":
                judgment = judge_dispute(...)
            ...
```

To:

```python
# NEW: fetch all sources, then one batch call
all_sources: list[SourceContent] = []
for sq in plan.queries:
    sources = search(...)
    all_sources.extend(sources)

# Deduplicate by URL
seen_urls: set[str] = set()
unique_sources = []
for src in all_sources:
    if src.url not in seen_urls:
        unique_sources.append(src)
        seen_urls.add(src.url)

deps = BatchVerifierDeps(
    settings=settings,
    article_name=fm.name,
    article_body=body,
    claims=plan.claims,
    sources=unique_sources,
    authoritative_domains=authoritative,
)
batch_findings = batch_verify(settings, deps)

# Convert BatchFinding → FindingTuple for the existing writer
findings = _batch_to_finding_tuples(batch_findings, plan.claims, unique_sources)
```

The writer and audit logging consume `FindingTuple` as before. A thin
adapter function `_batch_to_finding_tuples()` converts between formats.

### Source truncation

Each `SourceContent.content` is truncated to 4000 tokens (measured via
tiktoken) before being included in the prompt. With ~10 sources at 4K
each, the total prompt is ~45K tokens — well within gemini-flash's
context window.

### Model

Uses `settings.RESEARCH_EXECUTOR_MODEL` (same setting as the current
per-pair classifier). Default: `gemini-flash`.

## Expanded Authoritative Sources

`knowledge/.kebab/education.yaml` authoritative_sources expanded from
14 to 100 domains across 8 categories:

- Educational platforms (20): openstax, khanacademy, ck12, libretexts,
  pbslearningmedia, commonlit, edx, coursera, lumenlearning, etc.
- Government agencies (15): nasa, usgs, noaa, cdc, nih, epa, nps, etc.
- Museums/institutions (11): amnh, nhm.ac.uk, sciencemuseum, exploratorium, etc.
- Reference sites (10): britannica, nationalgeographic, bbc.co.uk, etc.
- Academic publishers (10): nature, sciencedirect, springer, jstor, etc.
- Universities (10): mit, stanford, harvard, open.edu, etc.
- Science news (10): sciencedaily, livescience, scientificamerican, etc.
- International (14): csiro.au, royalsociety.org, esa.int, etc.

No code changes — the YAML is read dynamically by `load_verticals()`.

## Testing Strategy

### Unit tests

- `test_batch_verifier.py` — mock the pydantic-ai agent, verify that
  `batch_verify()` returns one `BatchFinding` per claim. Test each
  outcome type (confirm, append, dispute, unverified).
- `test_batch_to_finding_tuples.py` — verify the adapter function
  correctly maps `BatchFinding` → `FindingTuple` with proper
  `dispute_category` handling.

### Integration tests

- Full research run with mocked LLM returning batch findings. Verify
  the writer produces correct confirm footnotes, appended sentences,
  and dispute entries.

### Expensive tests (`@pytest.mark.expensive`)

- Real LLM call with a sample article and 3 sources. Verify the batch
  output parses into valid `BatchVerifyResult`.

## Out of Scope

- **Parallelizing source fetches.** Still sequential (rate-limited).
  Could be a follow-up with `concurrent.futures.ThreadPoolExecutor`.
- **Batch planner.** The planner is already 1 LLM call — not a
  bottleneck.
- **Changing the writer.** The writer consumes `FindingTuple` as before.
  The adapter function bridges the gap.
