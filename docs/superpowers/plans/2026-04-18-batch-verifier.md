# Batch Verifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-claim, per-source classification loop in the research agent with a single batch LLM call, reducing ~24 sequential calls to 1.

**Architecture:** New `batch_verifier.py` module with a pydantic-ai agent that takes the full article + all claims + all sources and returns one `BatchFinding` per claim. The research orchestrator collects all sources first, deduplicates by URL, then calls `batch_verify()` once. A thin adapter converts `BatchFinding` → `FindingTuple` for the existing writer.

**Tech Stack:** Python 3.11+, pydantic v2, pydantic-ai, tiktoken

**Spec:** `docs/superpowers/specs/2026-04-18-batch-verifier-design.md`

---

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `app/agents/research/batch_verifier.py` | BatchFinding model, BatchVerifierDeps, batch_verify() function, adapter to FindingTuple |
| `app/agents/research/prompts/batch_verifier.md` | System prompt for the batch verifier agent |
| `tests/unit/agents/research/test_batch_verifier.py` | Tests for models, adapter, and agent call |

### Modified files
| File | Change |
|------|--------|
| `app/agents/research/research.py` | Replace per-pair loop (lines ~264-336) with fetch-all + batch_verify() + adapter |

---

### Task 1: BatchFinding model and adapter function

**Files:**
- Create: `app/agents/research/batch_verifier.py`
- Test: `tests/unit/agents/research/test_batch_verifier.py`

- [ ] **Step 1: Write failing tests for the model and adapter**

```python
# tests/unit/agents/research/test_batch_verifier.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.research.batch_verifier import (
    BatchFinding,
    BatchVerifyResult,
    batch_findings_to_tuples,
)
from app.agents.research.planner import ClaimEntry
from app.core.research.searcher import SourceContent


class TestBatchFinding:
    def test_confirm_finding(self) -> None:
        f = BatchFinding(
            claim_idx=0,
            outcome="confirm",
            source_title="Wikipedia: Plate tectonics",
            source_url="https://en.wikipedia.org/wiki/Plate_tectonics",
            evidence_quote="Plates move at 2-15cm/year.",
            reasoning="Source agrees with claim.",
        )
        assert f.outcome == "confirm"
        assert f.new_sentence is None
        assert f.contradiction is None

    def test_append_finding(self) -> None:
        f = BatchFinding(
            claim_idx=1,
            outcome="append",
            source_title="Britannica",
            source_url="https://britannica.com/science/plate-tectonics",
            evidence_quote="The fastest plate moves at 15cm/year.",
            new_sentence="The fastest-moving plate is the Pacific Plate at approximately 15 cm/year.",
            reasoning="Source adds quantitative detail.",
        )
        assert f.outcome == "append"
        assert f.new_sentence is not None

    def test_dispute_finding(self) -> None:
        f = BatchFinding(
            claim_idx=2,
            outcome="dispute",
            source_title="Wikipedia: Lystrosaurus",
            source_url="https://en.wikipedia.org/wiki/Lystrosaurus",
            evidence_quote="Found in Antarctica, India, China.",
            contradiction="Source excludes South America.",
            dispute_category="factual_error",
            reasoning="Claim includes South America which is not supported.",
        )
        assert f.outcome == "dispute"
        assert f.dispute_category == "factual_error"

    def test_unverified_finding(self) -> None:
        f = BatchFinding(
            claim_idx=3,
            outcome="unverified",
            source_title="",
            source_url="",
            evidence_quote="",
            reasoning="No source addresses this claim.",
        )
        assert f.outcome == "unverified"

    def test_normalizes_outcome_case(self) -> None:
        f = BatchFinding(
            claim_idx=0,
            outcome="CONFIRM",
            source_title="T",
            source_url="https://x.com",
            evidence_quote="E",
            reasoning="R",
        )
        assert f.outcome == "confirm"

    def test_normalizes_dispute_category(self) -> None:
        f = BatchFinding(
            claim_idx=0,
            outcome="dispute",
            source_title="T",
            source_url="https://x.com",
            evidence_quote="E",
            contradiction="C",
            dispute_category="Factual Error",
            reasoning="R",
        )
        assert f.dispute_category == "factual_error"

    def test_invalid_outcome_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BatchFinding(
                claim_idx=0,
                outcome="maybe",
                source_title="T",
                source_url="https://x.com",
                evidence_quote="E",
                reasoning="R",
            )


class TestBatchVerifyResult:
    def test_wraps_findings_list(self) -> None:
        r = BatchVerifyResult(findings=[
            BatchFinding(
                claim_idx=0, outcome="confirm",
                source_title="T", source_url="https://x.com",
                evidence_quote="E", reasoning="R",
            ),
        ])
        assert len(r.findings) == 1


class TestBatchFindingsToTuples:
    def test_converts_confirm(self) -> None:
        claims = [ClaimEntry(text="Plates move slowly.", section="Intro", paragraph=1)]
        sources = [SourceContent(title="Wiki", url="https://wiki.org", content="...")]
        batch = [
            BatchFinding(
                claim_idx=0, outcome="confirm",
                source_title="Wiki", source_url="https://wiki.org",
                evidence_quote="Plates move.", reasoning="Agrees.",
            ),
        ]
        tuples = batch_findings_to_tuples(batch, claims)
        assert len(tuples) == 1
        claim, finding, title, url = tuples[0]
        assert claim.text == "Plates move slowly."
        assert finding.outcome == "confirm"
        assert title == "Wiki"
        assert url == "https://wiki.org"

    def test_skips_unverified(self) -> None:
        claims = [ClaimEntry(text="Claim A.", section="S", paragraph=1)]
        batch = [
            BatchFinding(
                claim_idx=0, outcome="unverified",
                source_title="", source_url="",
                evidence_quote="", reasoning="No source.",
            ),
        ]
        tuples = batch_findings_to_tuples(batch, claims)
        assert tuples == []

    def test_suppresses_false_positive_disputes(self) -> None:
        claims = [ClaimEntry(text="Claim.", section="S", paragraph=1)]
        batch = [
            BatchFinding(
                claim_idx=0, outcome="dispute",
                source_title="T", source_url="https://x.com",
                evidence_quote="E", contradiction="C",
                dispute_category="false_positive",
                reasoning="R",
            ),
        ]
        tuples = batch_findings_to_tuples(batch, claims)
        assert tuples == []

    def test_suppresses_acceptable_simplification(self) -> None:
        claims = [ClaimEntry(text="Claim.", section="S", paragraph=1)]
        batch = [
            BatchFinding(
                claim_idx=0, outcome="dispute",
                source_title="T", source_url="https://x.com",
                evidence_quote="E", contradiction="C",
                dispute_category="acceptable_simplification",
                reasoning="R",
            ),
        ]
        tuples = batch_findings_to_tuples(batch, claims)
        assert tuples == []

    def test_surfaces_factual_error(self) -> None:
        claims = [ClaimEntry(text="Wrong claim.", section="S", paragraph=1)]
        batch = [
            BatchFinding(
                claim_idx=0, outcome="dispute",
                source_title="T", source_url="https://x.com",
                evidence_quote="E", contradiction="C",
                dispute_category="factual_error",
                reasoning="R",
            ),
        ]
        tuples = batch_findings_to_tuples(batch, claims)
        assert len(tuples) == 1
        assert tuples[0][1].dispute_category == "factual_error"

    def test_skips_out_of_range_claim_idx(self) -> None:
        claims = [ClaimEntry(text="Only claim.", section="S", paragraph=1)]
        batch = [
            BatchFinding(
                claim_idx=99, outcome="confirm",
                source_title="T", source_url="https://x.com",
                evidence_quote="E", reasoning="R",
            ),
        ]
        tuples = batch_findings_to_tuples(batch, claims)
        assert tuples == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/agents/research/test_batch_verifier.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement the models and adapter**

```python
# app/agents/research/batch_verifier.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.research.planner import ClaimEntry
from app.agents.research.verifier import (
    DisputeCategory,
    FindingResult,
    FindingTuple,
    SURFACED_CATEGORIES,
)
from app.config.config import Settings
from app.core.research.searcher import SourceContent

logger = logging.getLogger(__name__)


class BatchFinding(BaseModel):
    """One finding per claim from the batch verifier."""

    model_config = ConfigDict(extra="forbid")

    claim_idx: int = Field(..., description="Index into the claims list.")
    outcome: Literal["confirm", "append", "dispute", "unverified"] = Field(
        ..., description="How this claim relates to the sources."
    )
    source_title: str = Field(default="", description="Title of the most relevant source.")
    source_url: str = Field(default="", description="URL of the most relevant source.")
    evidence_quote: str = Field(default="", description="Direct passage from the source.")
    new_sentence: str | None = Field(
        default=None, description="New sentence to append (append outcome only)."
    )
    contradiction: str | None = Field(
        default=None, description="Description of contradiction (dispute outcome only)."
    )
    dispute_category: DisputeCategory | None = Field(
        default=None, description="Dispute classification (dispute outcome only)."
    )
    reasoning: str = Field(..., description="Why this classification.")

    @field_validator("outcome", mode="before")
    @classmethod
    def _normalize_outcome(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("dispute_category", mode="before")
    @classmethod
    def _normalize_category(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower().replace(" ", "_").replace("-", "_")
        return v


class BatchVerifyResult(BaseModel):
    """Output of the batch verifier agent."""

    model_config = ConfigDict(extra="forbid")

    findings: list[BatchFinding] = Field(
        ..., description="One finding per claim."
    )


@dataclass
class BatchVerifierDeps:
    """Runtime context for the batch verifier agent."""

    settings: Settings
    article_name: str
    article_body: str
    claims: list[ClaimEntry]
    sources: list[SourceContent]
    authoritative_domains: list[str]


def batch_findings_to_tuples(
    findings: list[BatchFinding],
    claims: list[ClaimEntry],
) -> list[FindingTuple]:
    """Convert batch findings to FindingTuples for the existing writer.

    Skips:
    - ``unverified`` outcomes (no finding to write)
    - Out-of-range ``claim_idx``
    - Disputes with suppressed categories (``false_positive``,
      ``acceptable_simplification``)
    """
    tuples: list[FindingTuple] = []
    for bf in findings:
        if bf.outcome == "unverified":
            continue
        if bf.claim_idx < 0 or bf.claim_idx >= len(claims):
            logger.warning(
                "batch_verifier: claim_idx %d out of range (%d claims) — skipping",
                bf.claim_idx,
                len(claims),
            )
            continue
        if (
            bf.outcome == "dispute"
            and bf.dispute_category is not None
            and bf.dispute_category not in SURFACED_CATEGORIES
        ):
            logger.info(
                "batch_verifier: suppressing %s dispute for claim %d",
                bf.dispute_category,
                bf.claim_idx,
            )
            continue

        finding = FindingResult(
            outcome=bf.outcome if bf.outcome != "unverified" else "confirm",
            reasoning=bf.reasoning,
            evidence_quote=bf.evidence_quote,
            new_sentence=bf.new_sentence,
            contradiction=bf.contradiction,
            dispute_category=bf.dispute_category,
        )
        tuples.append((claims[bf.claim_idx], finding, bf.source_title, bf.source_url))
    return tuples
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/agents/research/test_batch_verifier.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/agents/research/batch_verifier.py tests/unit/agents/research/test_batch_verifier.py
git commit -m "feat: BatchFinding model and batch_findings_to_tuples adapter"
```

---

### Task 2: Batch verifier agent + prompt

**Files:**
- Modify: `app/agents/research/batch_verifier.py`
- Create: `app/agents/research/prompts/batch_verifier.md`

- [ ] **Step 1: Write the batch verifier prompt**

```markdown
# app/agents/research/prompts/batch_verifier.md

# Research Batch Verifier

You verify an article's factual claims against a set of external sources.
For EVERY claim listed below, produce exactly one BatchFinding.

## Rules

1. For each claim, find the most relevant source. If multiple sources
   address the same claim, prefer the one from an authoritative domain
   (listed under "Authoritative domains").
2. Outcomes:
   - **confirm** — a source agrees with the claim. Cite the specific
     passage as evidence_quote.
   - **append** — a source has new, related information that the claim
     does not mention. Provide a new_sentence to add (1-2 sentences,
     factual, with no footnote markers). Do NOT append if the info is
     only tangentially related.
   - **dispute** — a source contradicts the claim. Classify into one of:
     - factual_error: the claim is factually wrong
     - misleading_simplification: simplified to the point of being wrong
     - contested_or_opinion: presents one side of a genuine debate
     - acceptable_simplification: simplified but not misleading — treat
       as confirm instead
     - false_positive: source and claim talk about different things —
       treat as unverified instead
   - **unverified** — no source addresses this claim at all.
3. EVERY claim MUST get exactly one finding. Do not skip claims.
4. evidence_quote MUST be a direct passage from the source text, not
   paraphrased. Copy it verbatim.
5. For dispute outcomes, provide both contradiction (what the source
   says differently) and dispute_category.
6. source_title and source_url must match one of the provided sources.
   For unverified outcomes, leave them as empty strings.
```

- [ ] **Step 2: Add `batch_verify()` function and `_build_batch_prompt()` to batch_verifier.py**

Add to the bottom of `app/agents/research/batch_verifier.py`:

```python
from pathlib import Path

from pydantic_ai import Agent

from app.core.llm.resolve import resolve_model

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def batch_verify(
    settings: Settings,
    deps: BatchVerifierDeps,
    *,
    agent: Agent[BatchVerifierDeps, BatchVerifyResult] | None = None,
) -> list[BatchFinding]:
    """Verify all claims against all sources in a single LLM call.

    Args:
        settings: KEBAB runtime configuration.
        deps: Article body, claims, sources, and authoritative domains.
        agent: Override for testing.

    Returns:
        One :class:`BatchFinding` per claim.
    """
    if agent is None:
        agent = Agent(
            model=resolve_model(settings.RESEARCH_EXECUTOR_MODEL),
            deps_type=BatchVerifierDeps,
            output_type=BatchVerifyResult,
            system_prompt=(_PROMPTS_DIR / "batch_verifier.md").read_text(),
        )

    user_prompt = _build_batch_prompt(deps)
    result = agent.run_sync(user_prompt, deps=deps)
    return result.output.findings


def _build_batch_prompt(deps: BatchVerifierDeps) -> str:
    """Assemble the user prompt with article, claims, and sources."""
    parts = [
        f"# Article: {deps.article_name}",
        "",
        deps.article_body,
        "",
        "## Claims to verify",
    ]
    for i, claim in enumerate(deps.claims):
        parts.append(f"{i}. \"{claim.text}\" (section: {claim.section}, paragraph: {claim.paragraph})")

    parts.append("")
    parts.append("## Sources")
    for src in deps.sources:
        # Truncate to ~4000 chars (~1000 tokens) per source
        content = src.content[:4000]
        parts.append(f"### {src.title} ({src.url})")
        parts.append(content)
        parts.append("")

    if deps.authoritative_domains:
        parts.append("## Authoritative domains (prefer these)")
        for domain in deps.authoritative_domains[:20]:
            parts.append(f"- {domain}")

    return "\n".join(parts)
```

- [ ] **Step 3: Run type checker**

Run: `uv run basedpyright app/agents/research/batch_verifier.py`
Expected: Clean

- [ ] **Step 4: Commit**

```bash
git add app/agents/research/batch_verifier.py app/agents/research/prompts/batch_verifier.md
git commit -m "feat: batch_verify() agent with one-shot verification prompt"
```

---

### Task 3: Wire batch verifier into research orchestrator

**Files:**
- Modify: `app/agents/research/research.py`
- Test: `tests/unit/agents/research/test_batch_verifier.py` (add integration-style test)

- [ ] **Step 1: Add test for the new orchestrator flow**

Add to `tests/unit/agents/research/test_batch_verifier.py`:

```python
class TestBatchVerifyIntegration:
    """Test that batch_verify is called correctly from research.run()."""

    def test_research_uses_batch_verify(self, mock_env, mocker, tmp_path) -> None:
        """research.run() should collect sources then call batch_verify once."""
        from app.agents.research import research as research_mod

        # Mock article lookup
        path = tmp_path / "art.md"
        mocker.patch.object(research_mod, "find_article_by_id", return_value=path)
        mock_fm = mocker.MagicMock()
        mock_fm.name = "Test Article"
        mock_fm.model_dump.return_value = {"contexts": {"education": {"grade": 10}}}
        mocker.patch.object(
            research_mod, "read_article",
            return_value=(mock_fm, "Body text.", mocker.MagicMock()),
        )
        mocker.patch.object(research_mod, "write_article")
        mocker.patch.object(research_mod, "resolve_vertical", return_value=mocker.MagicMock(authoritative_sources=["britannica.com"]))

        # Mock planner
        from app.agents.research.planner import ClaimEntry, ResearchPlan, SearchQuery
        plan = ResearchPlan(
            claims=[ClaimEntry(text="Claim A.", section="Intro", paragraph=1)],
            queries=[SearchQuery(query="claim A", adapter="tavily", target_claims=[0])],
        )
        mocker.patch.object(research_mod, "plan_research", return_value=plan)
        mocker.patch.object(research_mod, "_find_confirmed_claim_indices", return_value=set())

        # Mock search
        src = SourceContent(title="Brit", url="https://britannica.com/x", content="Source text.")
        mocker.patch.object(research_mod, "search", return_value=[src])

        # Mock batch_verify
        mock_batch = mocker.patch.object(
            research_mod, "batch_verify",
            return_value=[
                BatchFinding(
                    claim_idx=0, outcome="confirm",
                    source_title="Brit", source_url="https://britannica.com/x",
                    evidence_quote="Source text.", reasoning="Agrees.",
                ),
            ],
        )

        # Mock remaining helpers
        mocker.patch.object(research_mod, "batch_findings_to_tuples", return_value=[])
        mocker.patch.object(research_mod, "merge_appends", return_value=[])
        mocker.patch.object(research_mod, "apply_findings_to_article", return_value="Body text.")
        mocker.patch.object(research_mod, "parse_body", return_value=mocker.MagicMock())
        mocker.patch.object(research_mod, "count_external_footnotes", return_value=1)
        mocker.patch.object(research_mod, "extract_disputes", return_value=(0, 0))
        mocker.patch.object(research_mod, "next_footnote_number", return_value=1)
        mocker.patch.object(research_mod, "_write_unverified")
        mocker.patch.object(research_mod, "log_event")
        mocker.patch("app.agents.sync.auto_sync")

        from app.agents.research.research import run
        result = run(mock_env, article_id="TEST-001")

        mock_batch.assert_called_once()
```

- [ ] **Step 2: Replace the per-pair loop in `research.py`**

In `app/agents/research/research.py`, replace the section from line ~264 (after the planner filtering) to line ~336 (end of per-pair loop).

Add imports at the top:

```python
from app.agents.research.batch_verifier import (
    BatchFinding,
    BatchVerifierDeps,
    batch_findings_to_tuples,
    batch_verify,
)
```

Replace lines ~264-336 with:

```python
    # --- Fetch all sources across all queries ---
    all_sources: list[SourceContent] = []
    queries_run = 0
    for sq in plan.queries:
        if queries_run >= budget:
            logger.info("research: budget of %d queries reached", budget)
            break
        sources = search(
            settings, sq.adapter, sq.query, limit=2,
            authoritative_sources=authoritative,
        )
        queries_run += 1
        all_sources.extend(sources)

    # Deduplicate sources by URL
    seen_urls: set[str] = set()
    unique_sources: list[SourceContent] = []
    for src in all_sources:
        if src.url not in seen_urls:
            unique_sources.append(src)
            seen_urls.add(src.url)

    logger.info(
        "research: fetched %d unique sources for %d claims",
        len(unique_sources),
        len(plan.claims),
    )

    # --- Batch verify all claims against all sources ---
    if unique_sources:
        batch_deps = BatchVerifierDeps(
            settings=settings,
            article_name=fm.name,
            article_body=body,
            claims=plan.claims,
            sources=unique_sources,
            authoritative_domains=authoritative,
        )
        batch_findings = batch_verify(settings, batch_deps)
    else:
        batch_findings = []

    # Convert to FindingTuples and log audit events
    findings = batch_findings_to_tuples(batch_findings, plan.claims)

    confirmed_claims: set[int] = set()
    appended_claims: set[int] = set()
    disputed_claims: set[int] = set()

    for bf in batch_findings:
        if bf.outcome == "unverified" or bf.claim_idx >= len(plan.claims):
            continue
        claim = plan.claims[bf.claim_idx]
        if bf.outcome == "confirm":
            confirmed_claims.add(bf.claim_idx)
            log_event(
                path, stage="research", action="confirm",
                article_id=article_id,
                claim=claim.text, section=claim.section,
                source_title=bf.source_title, source_url=bf.source_url,
            )
        elif bf.outcome == "append":
            appended_claims.add(bf.claim_idx)
            log_event(
                path, stage="research", action="append",
                article_id=article_id,
                claim=claim.text, section=claim.section,
                new_sentence=bf.new_sentence or "",
                source_title=bf.source_title, source_url=bf.source_url,
            )
        elif bf.outcome == "dispute":
            if bf.dispute_category and bf.dispute_category not in SURFACED_CATEGORIES:
                log_event(
                    path, stage="research", action="dispute_suppressed",
                    article_id=article_id,
                    claim=claim.text, section=claim.section,
                    category=bf.dispute_category,
                    reasoning=bf.reasoning,
                    source_title=bf.source_title, source_url=bf.source_url,
                )
            else:
                disputed_claims.add(bf.claim_idx)
                log_event(
                    path, stage="research", action="dispute",
                    article_id=article_id,
                    claim=claim.text, section=claim.section,
                    category=bf.dispute_category or "",
                    contradiction=bf.contradiction or "",
                    reasoning=bf.reasoning,
                    source_title=bf.source_title, source_url=bf.source_url,
                )
```

Remove the old imports of `classify_finding` and `judge_dispute` from the top of the file (they're no longer called).

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/agents/research/ -v`
Expected: PASS

- [ ] **Step 4: Run full suite**

Run: `uv run pytest tests/ -m "not expensive and not ai and not network" -q`
Expected: PASS (minus the 3 pre-existing model preset failures)

- [ ] **Step 5: Run linters**

Run: `uv run ruff check app/agents/research/ && uv run basedpyright app/agents/research/`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add app/agents/research/research.py tests/unit/agents/research/test_batch_verifier.py
git commit -m "feat: wire batch verifier into research orchestrator — 1 LLM call replaces ~24"
```

---

### Task 4: Update docs and verify

**Files:**
- Modify: `docs/pipeline.md`

- [ ] **Step 1: Update the research stage documentation**

In `docs/pipeline.md`, update the Stage 4 (Research) section to reflect the new architecture:
- Replace the 4-file table (research.py, planner.py, verifier.py, writer.py) with 5 files including batch_verifier.py
- Update the flow diagram to show: plan → fetch all sources → batch verify (1 call) → write
- Note that verifier.py is preserved but no longer called by the orchestrator

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest tests/ -m "not expensive and not ai and not network" -q`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add docs/pipeline.md
git commit -m "docs: update research stage for batch verification"
```
