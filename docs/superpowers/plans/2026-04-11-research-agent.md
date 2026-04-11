# Research Agent Implementation Plan (M21)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous research agent that enriches and verifies articles against independent external sources (Wikipedia, OpenStax, Tavily), replacing the current verify stage.

**Architecture:** Two-stage agent (planner → executor). Planner extracts claims and generates search queries. Executor searches adapters, classifies findings as confirm/append/dispute, and modifies the article body. Confidence computed from external footnote count and dispute count.

**Tech Stack:** Python 3.11+, pydantic-ai, pydantic v2, existing adapter infrastructure (M18–M20)

**Spec:** `docs/superpowers/specs/2026-04-11-research-agent-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `app/agents/research/agent.py` (new) | Planner + executor orchestration, `run()` entry point |
| `app/agents/research/planner.py` (new) | Planner agent: extract claims, generate search plan |
| `app/agents/research/executor.py` (new) | Executor: search, compare, classify findings |
| `app/agents/research/prompts/planner.md` (new) | Planner system prompt |
| `app/agents/research/prompts/executor.md` (new) | Executor system prompt |
| `app/agents/research/prompts/dispute_judge.md` (new) | Dispute classification prompt |
| `app/core/confidence.py` | Update `compute_confidence` for new model |
| `app/core/markdown.py` | Add `extract_disputes()` and `count_external_footnotes()` helpers |
| `app/pipeline/ingest/inbox.py` (new) | `raw/inbox/` staging helpers |
| `app/cli.py` | Add `kebab agent research` command |
| `tests/unit/agents/test_research_planner.py` (new) | Planner unit tests |
| `tests/unit/agents/test_research_executor.py` (new) | Executor unit tests |
| `tests/unit/core/test_confidence_v2.py` (new) | New confidence computation tests |
| `tests/unit/core/test_markdown_research.py` (new) | External footnote/dispute parsing tests |
| `tests/integration/agents/test_research.py` (new) | Full research agent integration test |

---

### Task 1: Markdown Helpers for External Footnotes and Disputes

**Files:**
- Modify: `app/core/markdown.py`
- Create: `tests/unit/core/test_markdown_research.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for research-related markdown helpers."""

from __future__ import annotations

from app.core.markdown import count_external_footnotes, extract_disputes, next_footnote_number


class TestCountExternalFootnotes:
    def test_counts_http_footnotes(self) -> None:
        body = (
            "Claim one[^1]. Claim two[^2][^3].\n\n"
            "[^1]: [1] [Local Source](../raw/doc.pdf)\n"
            "[^2]: [Wikipedia: Plate tectonics](https://en.wikipedia.org/wiki/Plate_tectonics)\n"
            "[^3]: [OpenStax: Geology](https://openstax.org/books/geology/pages/1)\n"
        )
        assert count_external_footnotes(body) == 2

    def test_zero_when_no_external(self) -> None:
        body = (
            "Claim[^1].\n\n"
            "[^1]: [1] [Local](../raw/doc.pdf)\n"
        )
        assert count_external_footnotes(body) == 0

    def test_zero_when_no_footnotes(self) -> None:
        assert count_external_footnotes("Just a body.") == 0


class TestExtractDisputes:
    def test_extracts_dispute_entries(self) -> None:
        body = (
            "# Article\n\nContent.\n\n"
            "## Disputes\n\n"
            "- **Claim**: \"Convection is the sole driver\"\n"
            "  **Section**: Causes, paragraph 2\n"
            "  **External source**: [Wikipedia](https://...)\n"
            "  **Contradiction**: Slab pull is dominant.\n\n"
            "- **Claim**: \"All plates move at the same speed\"\n"
            "  **Section**: Movement, paragraph 1\n"
            "  **External source**: [OpenStax](https://...)\n"
            "  **Contradiction**: Speeds vary.\n"
        )
        disputes = extract_disputes(body)
        assert len(disputes) == 2

    def test_zero_when_no_disputes_section(self) -> None:
        assert extract_disputes("# Article\n\nContent.") == 0

    def test_zero_when_empty_disputes_section(self) -> None:
        assert extract_disputes("# Article\n\n## Disputes\n\n") == 0


class TestNextFootnoteNumber:
    def test_returns_next_after_highest(self) -> None:
        body = "Text[^1] more[^3].\n\n[^1]: src\n[^3]: src\n"
        assert next_footnote_number(body) == 4

    def test_returns_1_when_no_footnotes(self) -> None:
        assert next_footnote_number("No footnotes.") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/test_markdown_research.py -v`
Expected: FAIL — functions not found

- [ ] **Step 3: Implement the helpers**

Add to `app/core/markdown.py`:

```python
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^(\d+)\]:\s*(.+)$", re.MULTILINE)
_EXTERNAL_URL_RE = re.compile(r"https?://")


def count_external_footnotes(body: str) -> int:
    """Count footnote definitions that link to external URLs (http/https)."""
    count = 0
    for match in _FOOTNOTE_DEF_RE.finditer(body):
        if _EXTERNAL_URL_RE.search(match.group(2)):
            count += 1
    return count


def extract_disputes(body: str) -> int:
    """Count dispute entries in the ``## Disputes`` section."""
    section = extract_section(body, "Disputes")
    if not section:
        return 0
    return section.count("- **Claim**:")


def next_footnote_number(body: str) -> int:
    """Return the next available footnote number (max existing + 1)."""
    numbers = [int(m.group(1)) for m in _FOOTNOTE_DEF_RE.finditer(body)]
    return max(numbers, default=0) + 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/test_markdown_research.py -v`
Expected: all PASS

- [ ] **Step 5: Type check**

Run: `uv run basedpyright app/core/markdown.py`
Expected: 0 errors

---

### Task 2: Update Confidence Computation

**Files:**
- Modify: `app/core/confidence.py`
- Create: `tests/unit/core/test_confidence_v2.py`

- [ ] **Step 1: Write failing tests for new confidence model**

```python
"""Tests for research-based confidence computation."""

from __future__ import annotations

from app.core.confidence import compute_confidence
from app.models.frontmatter import FrontmatterSchema
from app.models.source import Source


def _fm(
    sources: int = 1,
    human_verified: bool = False,
    research_claims_total: int | None = None,
    external_confirms: int = 0,
    dispute_count: int = 0,
) -> FrontmatterSchema:
    """Build a minimal FrontmatterSchema for testing."""
    fm = FrontmatterSchema(
        id="TEST-001",
        name="Test",
        type="article",
        sources=[Source(id=i, title=f"src-{i}", tier=2) for i in range(sources)],
        human_verified=human_verified,
    )
    # Stamp research fields via extra="allow"
    dump = fm.model_dump()
    if research_claims_total is not None:
        dump["research_claims_total"] = research_claims_total
        dump["external_confirms"] = external_confirms
        dump["dispute_count"] = dispute_count
    return FrontmatterSchema.model_validate(dump)


class TestConfidenceV2:
    def test_level_0_no_sources(self) -> None:
        assert compute_confidence(_fm(sources=0)) == 0

    def test_level_1_has_sources_not_researched(self) -> None:
        assert compute_confidence(_fm(sources=2)) == 1

    def test_level_2_researched_below_threshold(self) -> None:
        # 5/10 = 50% < 70%
        assert compute_confidence(_fm(
            sources=2, research_claims_total=10, external_confirms=5, dispute_count=0
        )) == 2

    def test_level_2_researched_has_disputes(self) -> None:
        # 80% confirmed but has disputes → capped at 2
        assert compute_confidence(_fm(
            sources=2, research_claims_total=10, external_confirms=8, dispute_count=1
        )) == 2

    def test_level_3_researched_above_threshold_no_disputes(self) -> None:
        # 8/10 = 80% >= 70%, 0 disputes
        assert compute_confidence(_fm(
            sources=2, research_claims_total=10, external_confirms=8, dispute_count=0
        )) == 3

    def test_level_3_exact_threshold(self) -> None:
        # 7/10 = 70% exactly
        assert compute_confidence(_fm(
            sources=2, research_claims_total=10, external_confirms=7, dispute_count=0
        )) == 3

    def test_level_4_human_verified(self) -> None:
        assert compute_confidence(_fm(human_verified=True)) == 4

    def test_human_verified_overrides_disputes(self) -> None:
        assert compute_confidence(_fm(
            human_verified=True, research_claims_total=10, external_confirms=5, dispute_count=3
        )) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/test_confidence_v2.py -v`
Expected: some FAIL (old logic doesn't read research fields)

- [ ] **Step 3: Update `compute_confidence`**

Replace `app/core/confidence.py`:

```python
"""Confidence-level computation.

Pure function over :class:`FrontmatterSchema` — no I/O, no side effects.
The confidence gate (>=3) is the production threshold consumers should
honor; healthcare requires 4 (human verified).

Updated for research-based verification: confidence is derived from
external source confirmation rate and dispute count rather than
multi-LLM same-source checks.
"""

from __future__ import annotations

from app.models.confidence import ConfidenceLevel
from app.models.frontmatter import FrontmatterSchema

_CONFIRM_THRESHOLD = 0.70


def compute_confidence(fm: FrontmatterSchema) -> ConfidenceLevel:
    """Return the confidence level implied by ``fm``.

    Rules:
        4 — ``human_verified is True``
        3 — research ran, >=70% claims confirmed, 0 disputes
        2 — research ran, <70% confirmed OR has disputes
        1 — >=1 source, not yet researched
        0 — no sources
    """
    if fm.human_verified:
        return 4

    extras = fm.model_dump()
    research_total = extras.get("research_claims_total")
    if research_total is not None and research_total > 0:
        confirms = extras.get("external_confirms", 0)
        disputes = extras.get("dispute_count", 0)
        ratio = confirms / research_total
        if disputes == 0 and ratio >= _CONFIRM_THRESHOLD:
            return 3
        return 2

    # Legacy fallback: check old-style verification records.
    passed = sum(1 for record in fm.verifications if record.passed)
    if passed >= 2 and len(fm.sources) >= 2:
        return 3
    if passed >= 1:
        return 2

    if len(fm.sources) >= 1:
        return 1
    return 0
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/core/test_confidence_v2.py -v`
Expected: all PASS

- [ ] **Step 5: Run existing confidence tests to ensure backward compat**

Run: `uv run pytest tests/unit/core/test_confidence.py -v`
Expected: all PASS (legacy fallback path)

- [ ] **Step 6: Type check**

Run: `uv run basedpyright app/core/confidence.py`
Expected: 0 errors

---

### Task 3: Inbox Staging Helpers

**Files:**
- Create: `app/pipeline/ingest/inbox.py`
- Create: `tests/unit/pipeline/ingest/test_inbox.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for raw/inbox/ staging helpers."""

from __future__ import annotations

from pathlib import Path

from app.pipeline.ingest.inbox import inbox_path, list_inbox, stage_to_inbox


class TestInbox:
    def test_inbox_path(self, tmp_path: Path) -> None:
        assert inbox_path(tmp_path / "knowledge") == tmp_path / "knowledge" / "raw" / "inbox"

    def test_stage_to_inbox_creates_file(self, tmp_path: Path) -> None:
        knowledge = tmp_path / "knowledge"
        content = b"fake html content"
        path = stage_to_inbox(knowledge, "test-source.html", content)
        assert path.exists()
        assert path.read_bytes() == content
        assert path.parent == inbox_path(knowledge)

    def test_stage_to_inbox_creates_dir(self, tmp_path: Path) -> None:
        knowledge = tmp_path / "knowledge"
        stage_to_inbox(knowledge, "test.html", b"content")
        assert inbox_path(knowledge).is_dir()

    def test_list_inbox_empty(self, tmp_path: Path) -> None:
        knowledge = tmp_path / "knowledge"
        assert list_inbox(knowledge) == []

    def test_list_inbox_returns_files(self, tmp_path: Path) -> None:
        knowledge = tmp_path / "knowledge"
        stage_to_inbox(knowledge, "a.html", b"a")
        stage_to_inbox(knowledge, "b.html", b"b")
        items = list_inbox(knowledge)
        assert len(items) == 2
        assert {p.name for p in items} == {"a.html", "b.html"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/pipeline/ingest/test_inbox.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement inbox helpers**

```python
"""Staging helpers for ``raw/inbox/``.

External sources found by the research agent are staged here before
being promoted to ``raw/documents/``. Each file gets a provenance
sidecar via the standard ``write_sidecar`` path.
"""

from __future__ import annotations

from pathlib import Path


def inbox_path(knowledge_dir: Path) -> Path:
    """Return the inbox directory path."""
    return knowledge_dir / "raw" / "inbox"


def stage_to_inbox(knowledge_dir: Path, filename: str, content: bytes) -> Path:
    """Write ``content`` to ``raw/inbox/<filename>``. Returns the path."""
    target = inbox_path(knowledge_dir) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def list_inbox(knowledge_dir: Path) -> list[Path]:
    """Return all files in the inbox, sorted by name."""
    inbox = inbox_path(knowledge_dir)
    if not inbox.exists():
        return []
    return sorted(p for p in inbox.iterdir() if p.is_file() and not p.name.endswith(".meta.json"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/pipeline/ingest/test_inbox.py -v`
Expected: all PASS

- [ ] **Step 5: Type check**

Run: `uv run basedpyright app/pipeline/ingest/inbox.py`
Expected: 0 errors

---

### Task 4: Research Planner Agent

**Files:**
- Create: `app/agents/research/__init__.py` (empty)
- Create: `app/agents/research/planner.py`
- Create: `app/agents/research/prompts/planner.md`
- Create: `tests/unit/agents/test_research_planner.py`

- [ ] **Step 1: Create prompts directory and planner prompt**

Create `app/agents/research/prompts/planner.md`:

```markdown
# Research Planner

You analyze a curated article and produce a research plan for external verification and enrichment.

## Input

- `article_name`: title of the article.
- `article_body`: full markdown body.
- `available_adapters`: list of adapter names (e.g. ["wikipedia", "openstax", "tavily"]).
- `budget_hint`: approximate number of searches allowed.

## Output (ResearchPlan)

- `claims`: list of factual claims extracted from the article. Each has:
  - `text`: the claim statement
  - `section`: the markdown section heading it appears under
  - `paragraph`: paragraph number within that section (1-based)
- `queries`: list of search queries to run. Each has:
  - `query`: the search string
  - `adapter`: which adapter to use ("wikipedia", "openstax", or "tavily")
  - `target_claims`: list of claim indices this query aims to verify (0-based)

## Rules

1. Extract EVERY non-trivial factual claim. Skip definitions, section headers, and transitional text.
2. Generate targeted queries — not the article title verbatim. Each query should find sources that can confirm or deny specific claims.
3. Prefer high-tier adapters: openstax (tier 2) for educational content, wikipedia (tier 4) for general facts, tavily (tier 4) for everything else.
4. Stay within budget_hint for total query count.
5. Each claim should be targeted by at least one query.
```

- [ ] **Step 2: Write failing tests for planner**

```python
"""Tests for the research planner agent."""

from __future__ import annotations

from app.agents.research.planner import (
    ClaimEntry,
    ResearchPlan,
    SearchQuery,
    plan_research,
    PlannerDeps,
)
from app.config.config import Settings


def _stub_planner(
    _settings: Settings, _deps: PlannerDeps
) -> ResearchPlan:
    return ResearchPlan(
        claims=[
            ClaimEntry(text="Plates move due to convection", section="Causes", paragraph=1),
            ClaimEntry(text="Slab pull is a mechanism", section="Causes", paragraph=2),
        ],
        queries=[
            SearchQuery(query="plate tectonics convection", adapter="wikipedia", target_claims=[0]),
            SearchQuery(query="slab pull mechanism", adapter="openstax", target_claims=[1]),
        ],
    )


class TestResearchPlan:
    def test_plan_has_claims_and_queries(self) -> None:
        plan = _stub_planner(None, None)
        assert len(plan.claims) == 2
        assert len(plan.queries) == 2

    def test_each_claim_has_text_and_location(self) -> None:
        plan = _stub_planner(None, None)
        claim = plan.claims[0]
        assert claim.text == "Plates move due to convection"
        assert claim.section == "Causes"
        assert claim.paragraph == 1

    def test_each_query_targets_claims(self) -> None:
        plan = _stub_planner(None, None)
        q = plan.queries[0]
        assert q.adapter == "wikipedia"
        assert 0 in q.target_claims

    def test_claim_entry_model_validates(self) -> None:
        entry = ClaimEntry(text="test", section="Intro", paragraph=1)
        assert entry.text == "test"

    def test_search_query_model_validates(self) -> None:
        q = SearchQuery(query="test", adapter="wikipedia", target_claims=[0])
        assert q.adapter == "wikipedia"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/agents/test_research_planner.py -v`
Expected: FAIL — imports not found

- [ ] **Step 4: Implement planner**

Create `app/agents/research/__init__.py` (empty file).

Create `app/agents/research/planner.py`:

```python
"""Research planner — extracts claims and generates search queries.

Stage 1 of the research agent. Reads an article body and produces
a structured research plan: what to search for, where, and which
claims each query targets.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.config.config import Settings
from app.core.llm import resolve_model

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "planner.md"


class ClaimEntry(BaseModel):
    """One factual claim extracted from the article."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., description="The claim statement.")
    section: str = Field(..., description="Markdown section heading.")
    paragraph: int = Field(..., ge=1, description="Paragraph number within the section.")


class SearchQuery(BaseModel):
    """One search query targeting specific claims."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., description="The search string.")
    adapter: str = Field(..., description="Adapter name: wikipedia, openstax, or tavily.")
    target_claims: list[int] = Field(..., description="Indices of claims this query aims to verify.")


class ResearchPlan(BaseModel):
    """Output of the planner agent."""

    model_config = ConfigDict(extra="forbid")

    claims: list[ClaimEntry] = Field(..., description="Extracted factual claims.")
    queries: list[SearchQuery] = Field(..., description="Search queries to execute.")


@dataclass
class PlannerDeps:
    """Runtime context for the planner agent."""

    settings: Settings
    article_name: str
    article_body: str
    available_adapters: list[str]
    budget_hint: int


def _build_planner_agent(settings: Settings) -> Agent[PlannerDeps, ResearchPlan]:
    return Agent(
        model=resolve_model(settings.LLM_CURATION_MODEL),
        deps_type=PlannerDeps,
        output_type=ResearchPlan,
        system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def plan_research(
    settings: Settings,
    deps: PlannerDeps,
    *,
    agent: Agent[PlannerDeps, ResearchPlan] | None = None,
) -> ResearchPlan:
    """Run the planner agent and return a research plan."""
    agent = agent or _build_planner_agent(settings)
    user = (
        f"article_name: {deps.article_name}\n\n"
        f"available_adapters: {deps.available_adapters}\n"
        f"budget_hint: {deps.budget_hint}\n\n"
        f"article_body:\n{deps.article_body}"
    )
    return agent.run_sync(user, deps=deps).output
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/agents/test_research_planner.py -v`
Expected: all PASS

- [ ] **Step 6: Type check**

Run: `uv run basedpyright app/agents/research/planner.py`
Expected: 0 errors

---

### Task 5: Research Executor

**Files:**
- Create: `app/agents/research/executor.py`
- Create: `app/agents/research/prompts/executor.md`
- Create: `app/agents/research/prompts/dispute_judge.md`
- Create: `tests/unit/agents/test_research_executor.py`

- [ ] **Step 1: Create executor prompt**

Create `app/agents/research/prompts/executor.md`:

```markdown
# Research Executor

You evaluate whether external source content confirms, enriches, or contradicts claims in a curated article.

## Input

- `claim`: the factual claim to evaluate.
- `claim_section`: which section the claim is in.
- `source_title`: title of the external source.
- `source_content`: text content of the external source.

## Output (FindingResult)

- `outcome`: one of "confirm", "append", "dispute"
- `reasoning`: brief explanation of the classification.
- `evidence_quote`: the specific passage from the source that supports your classification.
- `new_sentence`: if outcome is "append", the new sentence to add to the article. Must be grounded in the source. Null for confirm/dispute.
- `contradiction`: if outcome is "dispute", a clear description of the conceptual disagreement. Null for confirm/append.

## Rules

1. "confirm" means the source says essentially the same thing as the claim.
2. "append" means the source has relevant NEW information not in the article. The new_sentence must be factual and cite-worthy.
3. "dispute" means the source CONTRADICTS the claim — a genuine factual disagreement, not a phrasing difference.
4. If the source is irrelevant to the claim, do not return a finding.
5. Be strict about "dispute" — only flag genuine conceptual contradictions.
```

Create `app/agents/research/prompts/dispute_judge.md`:

```markdown
# Dispute Judge

You determine whether a disagreement between an article claim and an external source is a genuine conceptual dispute or a superficial difference.

## Input

- `claim`: the article's claim.
- `source_content`: what the external source says.
- `initial_reasoning`: why the executor flagged this as a dispute.

## Output (DisputeJudgment)

- `is_genuine`: true if this is a real factual contradiction, false if it's a phrasing/scope/emphasis difference.
- `reasoning`: explanation of your judgment.
- `summary`: if genuine, a concise description of the disagreement for the Disputes section.

## Rules

1. Phrasing differences are NOT disputes. "Primary driver" vs "major factor" is emphasis, not contradiction.
2. Scope differences are NOT disputes. A source covering a broader topic may not mention a specific detail — that's not a contradiction.
3. A dispute requires the source to assert something INCOMPATIBLE with the claim.
```

- [ ] **Step 2: Write failing tests for executor**

```python
"""Tests for the research executor."""

from __future__ import annotations

from app.agents.research.executor import (
    DisputeJudgment,
    FindingResult,
    classify_finding,
    apply_findings_to_article,
)
from app.agents.research.planner import ClaimEntry


class TestFindingResult:
    def test_confirm_finding(self) -> None:
        f = FindingResult(
            outcome="confirm",
            reasoning="Source agrees.",
            evidence_quote="Plates move due to convection.",
        )
        assert f.outcome == "confirm"
        assert f.new_sentence is None

    def test_append_finding(self) -> None:
        f = FindingResult(
            outcome="append",
            reasoning="New info.",
            evidence_quote="Ridge push also contributes.",
            new_sentence="Ridge push at mid-ocean ridges also contributes to plate movement.",
        )
        assert f.outcome == "append"
        assert f.new_sentence is not None

    def test_dispute_finding(self) -> None:
        f = FindingResult(
            outcome="dispute",
            reasoning="Source contradicts.",
            evidence_quote="Slab pull is dominant.",
            contradiction="Source says slab pull, not convection, is the primary driver.",
        )
        assert f.outcome == "dispute"


class TestApplyFindings:
    def test_append_adds_sentence_with_footnote(self) -> None:
        body = "# Topic\n\nExisting content[^1].\n\n[^1]: [1] [Local](path.pdf)\n"
        findings = [
            (
                ClaimEntry(text="Existing content", section="Topic", paragraph=1),
                FindingResult(
                    outcome="append",
                    reasoning="new",
                    evidence_quote="quote",
                    new_sentence="New fact from external source.",
                ),
                "Wikipedia: Topic",
                "https://en.wikipedia.org/wiki/Topic",
            ),
        ]
        result = apply_findings_to_article(body, findings)
        assert "New fact from external source." in result
        assert "[^2]" in result
        assert "https://en.wikipedia.org/wiki/Topic" in result

    def test_confirm_adds_footnote_only(self) -> None:
        body = "# Topic\n\nExisting content[^1].\n\n[^1]: [1] [Local](path.pdf)\n"
        findings = [
            (
                ClaimEntry(text="Existing content", section="Topic", paragraph=1),
                FindingResult(
                    outcome="confirm",
                    reasoning="agrees",
                    evidence_quote="Same fact.",
                ),
                "Wikipedia: Topic",
                "https://en.wikipedia.org/wiki/Topic",
            ),
        ]
        result = apply_findings_to_article(body, findings)
        assert "[^2]" in result
        assert "https://en.wikipedia.org/wiki/Topic" in result
        assert "New fact" not in result  # no new sentence

    def test_dispute_adds_disputes_section(self) -> None:
        body = "# Topic\n\nClaim text[^1].\n\n[^1]: [1] [Local](path.pdf)\n"
        findings = [
            (
                ClaimEntry(text="Claim text", section="Topic", paragraph=1),
                FindingResult(
                    outcome="dispute",
                    reasoning="contradicts",
                    evidence_quote="Opposite fact.",
                    contradiction="Source says the opposite.",
                ),
                "Wikipedia: Topic",
                "https://en.wikipedia.org/wiki/Topic",
            ),
        ]
        result = apply_findings_to_article(body, findings)
        assert "## Disputes" in result
        assert "**Claim**:" in result
        assert "Source says the opposite." in result
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/agents/test_research_executor.py -v`
Expected: FAIL — imports not found

- [ ] **Step 4: Implement executor**

Create `app/agents/research/executor.py`:

```python
"""Research executor — searches external sources and classifies findings.

Stage 2 of the research agent. Runs the search plan from the planner,
evaluates each finding against article claims, and modifies the article
body with confirmations, appended facts, and disputes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.agents.research.planner import ClaimEntry, ResearchPlan, SearchQuery
from app.config.config import Settings
from app.core.llm import resolve_model
from app.core.markdown import next_footnote_number

logger = logging.getLogger(__name__)

_EXECUTOR_PROMPT_PATH = Path(__file__).parent / "prompts" / "executor.md"
_JUDGE_PROMPT_PATH = Path(__file__).parent / "prompts" / "dispute_judge.md"


class FindingResult(BaseModel):
    """Classification of one external source finding against a claim."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["confirm", "append", "dispute"] = Field(
        ..., description="How this finding relates to the claim."
    )
    reasoning: str = Field(..., description="Why this classification.")
    evidence_quote: str = Field(..., description="Specific passage from the source.")
    new_sentence: str | None = Field(
        default=None, description="New sentence to append (append outcome only)."
    )
    contradiction: str | None = Field(
        default=None, description="Description of contradiction (dispute outcome only)."
    )


class DisputeJudgment(BaseModel):
    """Whether a flagged dispute is genuine or superficial."""

    model_config = ConfigDict(extra="forbid")

    is_genuine: bool = Field(..., description="True if real contradiction.")
    reasoning: str = Field(..., description="Explanation.")
    summary: str = Field(default="", description="Concise dispute description if genuine.")


@dataclass
class ExecutorDeps:
    settings: Settings
    claim_text: str
    claim_section: str
    source_title: str
    source_content: str


@dataclass
class JudgeDeps:
    settings: Settings
    claim: str
    source_content: str
    initial_reasoning: str


# Type alias for a finding tuple: (claim, finding, source_title, source_url)
FindingTuple = tuple[ClaimEntry, FindingResult, str, str]


def _build_executor_agent(settings: Settings) -> Agent[ExecutorDeps, FindingResult]:
    return Agent(
        model=resolve_model(settings.FAST_MODEL),
        deps_type=ExecutorDeps,
        output_type=FindingResult,
        system_prompt=_EXECUTOR_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def _build_judge_agent(settings: Settings) -> Agent[JudgeDeps, DisputeJudgment]:
    return Agent(
        model=resolve_model(settings.LLM_CURATION_MODEL),
        deps_type=JudgeDeps,
        output_type=DisputeJudgment,
        system_prompt=_JUDGE_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def classify_finding(
    settings: Settings,
    claim: ClaimEntry,
    source_title: str,
    source_content: str,
    *,
    agent: Agent[ExecutorDeps, FindingResult] | None = None,
) -> FindingResult:
    """Classify a single source against a single claim."""
    agent = agent or _build_executor_agent(settings)
    deps = ExecutorDeps(
        settings=settings,
        claim_text=claim.text,
        claim_section=claim.section,
        source_title=source_title,
        source_content=source_content,
    )
    user = (
        f"claim: {claim.text}\n"
        f"claim_section: {claim.section}\n"
        f"source_title: {source_title}\n\n"
        f"source_content:\n{source_content[:8000]}"
    )
    return agent.run_sync(user, deps=deps).output


def judge_dispute(
    settings: Settings,
    claim: ClaimEntry,
    finding: FindingResult,
    source_content: str,
    *,
    agent: Agent[JudgeDeps, DisputeJudgment] | None = None,
) -> DisputeJudgment:
    """Determine if a flagged dispute is genuine."""
    agent = agent or _build_judge_agent(settings)
    deps = JudgeDeps(
        settings=settings,
        claim=claim.text,
        source_content=source_content,
        initial_reasoning=finding.reasoning,
    )
    user = (
        f"claim: {claim.text}\n"
        f"initial_reasoning: {finding.reasoning}\n"
        f"evidence_quote: {finding.evidence_quote}\n\n"
        f"source_content:\n{source_content[:4000]}"
    )
    return agent.run_sync(user, deps=deps).output


def apply_findings_to_article(
    body: str,
    findings: list[FindingTuple],
) -> str:
    """Apply confirmed/appended/disputed findings to the article body.

    - confirm: add footnote citation to the claim's sentence
    - append: add new sentence with footnote after the relevant section
    - dispute: add entry to ## Disputes section
    """
    footnote_num = next_footnote_number(body)
    new_footnote_defs: list[str] = []
    disputes: list[str] = []
    appends: dict[str, list[str]] = {}  # section → sentences to append

    for claim, finding, source_title, source_url in findings:
        if finding.outcome == "confirm":
            # Add footnote ref to the sentence containing the claim
            ref = f"[^{footnote_num}]"
            # Try to find the claim text and add the ref
            escaped = re.escape(claim.text)
            pattern = re.compile(f"({escaped})")
            if pattern.search(body):
                body = pattern.sub(rf"\1{ref}", body, count=1)
            new_footnote_defs.append(
                f"[^{footnote_num}]: [{source_title}]({source_url})"
            )
            footnote_num += 1

        elif finding.outcome == "append" and finding.new_sentence:
            ref = f"[^{footnote_num}]"
            sentence = f"{finding.new_sentence}{ref}"
            appends.setdefault(claim.section, []).append(sentence)
            new_footnote_defs.append(
                f"[^{footnote_num}]: [{source_title}]({source_url})"
            )
            footnote_num += 1

        elif finding.outcome == "dispute" and finding.contradiction:
            disputes.append(
                f"- **Claim**: \"{claim.text}\"\n"
                f"  **Section**: {claim.section}, paragraph {claim.paragraph}\n"
                f"  **External source**: [{source_title}]({source_url})\n"
                f"  **Contradiction**: {finding.contradiction}"
            )

    # Apply appends: add sentences at end of their section
    for section, sentences in appends.items():
        section_pattern = re.compile(
            rf"(^##\s+{re.escape(section)}\s*\n.*?)(?=^##\s+|\Z)",
            re.DOTALL | re.MULTILINE,
        )
        match = section_pattern.search(body)
        if match:
            insert_text = "\n" + " ".join(sentences) + "\n"
            body = body[:match.end(1)] + insert_text + body[match.end(1):]

    # Split body from existing footnote defs
    parts = body.rsplit("\n\n[^", 1)
    if len(parts) == 2:
        main_body = parts[0] + "\n\n[^" + parts[1].rstrip()
    else:
        main_body = body.rstrip()

    # Add disputes section
    if disputes:
        main_body += "\n\n## Disputes\n\n" + "\n\n".join(disputes)

    # Add new footnote definitions
    if new_footnote_defs:
        main_body += "\n" + "\n".join(new_footnote_defs)

    return main_body + "\n"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/agents/test_research_executor.py -v`
Expected: all PASS

- [ ] **Step 6: Type check**

Run: `uv run basedpyright app/agents/research/executor.py`
Expected: 0 errors

---

### Task 6: Research Agent Orchestrator

**Files:**
- Create: `app/agents/research/agent.py`
- Create: `tests/integration/agents/test_research.py`

- [ ] **Step 1: Write integration test with stubbed agents**

```python
"""Integration test for the research agent orchestrator."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.research.agent import ResearchResult, run
from app.agents.research.executor import FindingResult
from app.agents.research.planner import ClaimEntry, ResearchPlan, SearchQuery
from app.config.config import Settings
from app.core.markdown import read_article, write_article
from app.core.source_index import SourceIndex, SourceEntry, save_index
from app.models.frontmatter import FrontmatterSchema
from app.models.source import Source


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    curated = knowledge / "curated" / "Science"
    curated.mkdir(parents=True)
    (knowledge / "raw" / "documents").mkdir(parents=True)
    (knowledge / "raw" / "inbox").mkdir(parents=True)
    (knowledge / "processed" / "documents").mkdir(parents=True)
    (knowledge / ".kebab").mkdir(parents=True)

    # Write a source index
    index = SourceIndex(
        sources=[SourceEntry(id=1, stem="test", raw_path="raw/documents/test.pdf",
                             title="Test Source", tier=1, checksum="abc", adapter="local_pdf")],
        next_id=2,
    )
    save_index(index, knowledge / ".kebab" / "sources.json")

    # Write a minimal article
    fm = FrontmatterSchema(
        id="SCI-001", name="Plate Tectonics", type="article",
        sources=[Source(id=1, title="Test Source", tier=1)],
    )
    body = (
        "# Plate Tectonics\n\n"
        "Plates move due to convection currents[^1].\n\n"
        "[^1]: [1] [Test Source](../../raw/documents/test.pdf)\n"
    )
    write_article(curated / "plate-tectonics.md", fm, body)

    return Settings(
        KNOWLEDGE_DIR=knowledge, RAW_DIR=knowledge / "raw",
        PROCESSED_DIR=knowledge / "processed",
        CURATED_DIR=knowledge / "curated",
        QDRANT_PATH=None, QDRANT_URL=None, GOOGLE_API_KEY="test-key",
    )


def _stub_planner(_settings, _deps):
    return ResearchPlan(
        claims=[ClaimEntry(text="Plates move due to convection currents", section="Plate Tectonics", paragraph=1)],
        queries=[SearchQuery(query="plate tectonics convection", adapter="wikipedia", target_claims=[0])],
    )


def _stub_searcher(_adapter_name, _query, _settings):
    """Return mock search results: (title, url, content)."""
    return [("Wikipedia: Plate tectonics", "https://en.wikipedia.org/wiki/Plate_tectonics",
             "Convection currents in the mantle drive plate movement. Ridge push also contributes.")]


def _stub_classifier(_settings, _claim, _source_title, _source_content):
    return FindingResult(
        outcome="confirm",
        reasoning="Source confirms convection drives plates.",
        evidence_quote="Convection currents in the mantle drive plate movement.",
    )


@pytest.mark.integration
def test_research_enriches_article(settings: Settings) -> None:
    result = run(
        settings,
        article_id="SCI-001",
        planner=_stub_planner,
        searcher=_stub_searcher,
        classifier=_stub_classifier,
    )
    assert len(result.findings) >= 1
    # Article should have a new external footnote
    fm, body = read_article(settings.CURATED_DIR / "Science" / "plate-tectonics.md")
    assert "wikipedia.org" in body
    # Frontmatter should have research metadata
    dump = fm.model_dump()
    assert dump.get("research_claims_total") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/agents/test_research.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement orchestrator**

Create `app/agents/research/agent.py`:

```python
"""Research agent — enriches and verifies articles against external sources.

Two-stage architecture:
1. Planner — extracts claims and generates search queries
2. Executor — searches, classifies findings, modifies the article

Replaces the verify stage with independent-source verification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from app.agents.research.executor import (
    FindingResult,
    FindingTuple,
    apply_findings_to_article,
    classify_finding,
    judge_dispute,
)
from app.agents.research.planner import (
    ClaimEntry,
    PlannerDeps,
    ResearchPlan,
    SearchQuery,
    plan_research,
)
from app.config.config import Settings
from app.core.markdown import count_external_footnotes, extract_disputes, read_article, write_article
from app.core.source_adapter import Candidate
from app.models.frontmatter import FrontmatterSchema
from app.pipeline.ingest.inbox import stage_to_inbox
from app.pipeline.ingest.registry import AdapterRegistry, build_default_registry

logger = logging.getLogger(__name__)

# Type aliases for pluggable stubs in tests
PlannerFn = Callable[..., ResearchPlan]
SearcherFn = Callable[[str, str, Settings], list[tuple[str, str, str]]]
ClassifierFn = Callable[..., FindingResult]


@dataclass
class ResearchResult:
    """Summary of a research run on one article."""

    article_id: str
    findings: list[FindingTuple] = field(default_factory=list)
    confirms: int = 0
    appends: int = 0
    disputes: int = 0
    claims_total: int = 0


def _default_searcher(
    adapter_name: str, query: str, settings: Settings
) -> list[tuple[str, str, str]]:
    """Search using the adapter registry. Returns [(title, url, content)]."""
    registry = build_default_registry(settings)
    try:
        adapter = registry.get(adapter_name)
    except Exception:
        logger.warning("research: adapter %s not available — skipping", adapter_name)
        return []

    candidates = adapter.discover(query, limit=3)
    results: list[tuple[str, str, str]] = []
    for cand in candidates[:2]:  # fetch top 2
        try:
            artifact = adapter.fetch(cand)
            content = artifact.raw_path.read_text(encoding="utf-8", errors="replace")
            url = cand.locator if cand.locator.startswith("http") else f"https://en.wikipedia.org/wiki/{cand.locator}"
            results.append((cand.title, url, content))
            # Stage in inbox for provenance
            stage_to_inbox(
                Path(settings.KNOWLEDGE_DIR),
                artifact.raw_path.name,
                artifact.raw_path.read_bytes(),
            )
        except Exception as exc:
            logger.warning("research: fetch failed for %s: %s", cand.title, exc)
    return results


def _find_article_path(settings: Settings, article_id: str) -> Path | None:
    """Find the markdown file for an article by ID."""
    curated = Path(settings.CURATED_DIR)
    if not curated.exists():
        return None
    for md in curated.rglob("*.md"):
        try:
            fm, _ = read_article(md)
            if fm.id == article_id:
                return md
        except Exception:
            continue
    return None


def run(
    settings: Settings,
    *,
    article_id: str,
    planner: PlannerFn = plan_research,
    searcher: SearcherFn = _default_searcher,
    classifier: ClassifierFn = classify_finding,
    budget: int = 10,
) -> ResearchResult:
    """Research a single article: plan, search, classify, modify."""
    path = _find_article_path(settings, article_id)
    if path is None:
        logger.error("research: article %s not found", article_id)
        return ResearchResult(article_id=article_id)

    fm, body = read_article(path)

    # Available adapters
    available = ["wikipedia", "openstax"]
    tavily_key = getattr(settings, "TAVILY_API_KEY", "")
    if tavily_key:
        available.append("tavily")

    # Stage 1: Plan
    deps = PlannerDeps(
        settings=settings,
        article_name=fm.name,
        article_body=body,
        available_adapters=available,
        budget_hint=budget,
    )
    plan = planner(settings, deps)

    result = ResearchResult(
        article_id=article_id,
        claims_total=len(plan.claims),
    )

    # Stage 2: Execute
    findings: list[FindingTuple] = []
    for query in plan.queries:
        search_results = searcher(query.adapter, query.query, settings)
        for source_title, source_url, source_content in search_results:
            for claim_idx in query.target_claims:
                if claim_idx >= len(plan.claims):
                    continue
                claim = plan.claims[claim_idx]
                try:
                    finding = classifier(settings, claim, source_title, source_content)
                except Exception as exc:
                    logger.warning("research: classify failed for claim %d: %s", claim_idx, exc)
                    continue

                # Judge disputes
                if finding.outcome == "dispute":
                    try:
                        judgment = judge_dispute(settings, claim, finding, source_content)
                        if not judgment.is_genuine:
                            logger.info("research: dispute for claim %d judged superficial — skipping", claim_idx)
                            continue
                        finding = FindingResult(
                            outcome="dispute",
                            reasoning=finding.reasoning,
                            evidence_quote=finding.evidence_quote,
                            contradiction=judgment.summary or finding.contradiction,
                        )
                    except Exception as exc:
                        logger.warning("research: judge failed for claim %d: %s", claim_idx, exc)

                findings.append((claim, finding, source_title, source_url))

                if finding.outcome == "confirm":
                    result.confirms += 1
                elif finding.outcome == "append":
                    result.appends += 1
                elif finding.outcome == "dispute":
                    result.disputes += 1

    result.findings = findings

    # Apply findings to article body
    if findings:
        new_body = apply_findings_to_article(body, findings)

        # Update frontmatter with research metadata
        fm_dump: dict[str, Any] = fm.model_dump()
        fm_dump["research_claims_total"] = result.claims_total
        fm_dump["external_confirms"] = count_external_footnotes(new_body)
        fm_dump["dispute_count"] = extract_disputes(new_body)
        fm_dump["researched_at"] = datetime.now().date().isoformat()
        new_fm = FrontmatterSchema.model_validate(fm_dump)
        write_article(path, new_fm, new_body)

    logger.info(
        "research %s: %d claims, %d confirmed, %d appended, %d disputed",
        article_id, result.claims_total, result.confirms, result.appends, result.disputes,
    )
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/agents/test_research.py -v`
Expected: PASS

- [ ] **Step 5: Type check**

Run: `uv run basedpyright app/agents/research/agent.py`
Expected: 0 errors

---

### Task 7: CLI Commands

**Files:**
- Modify: `app/cli.py`

- [ ] **Step 1: Add research commands to CLI**

In `app/cli.py`, add under the `agent` group:

```python
@agent.command("research")
@click.argument("article_id", required=False)
@click.option("--all", "research_all", is_flag=True, help="Research all articles.")
@click.option("--budget", type=int, default=10, show_default=True, help="Max queries per article.")
def agent_research(article_id: str | None, research_all: bool, budget: int) -> None:
    """Enrich and verify an article against external sources."""
    from app.agents.research import agent as research_agent

    if research_all:
        curated = Path(env.CURATED_DIR)
        if not curated.exists():
            raise click.ClickException("no curated articles found")
        from app.core.markdown import read_article
        for md in sorted(curated.rglob("*.md")):
            try:
                fm, _ = read_article(md)
            except Exception:
                continue
            result = research_agent.run(env, article_id=fm.id, budget=budget)
            click.echo(
                f"  {fm.id}: {result.confirms} confirmed, "
                f"{result.appends} appended, {result.disputes} disputed"
            )
    elif article_id:
        result = research_agent.run(env, article_id=article_id, budget=budget)
        click.echo(
            f"research {article_id}: {result.claims_total} claims, "
            f"{result.confirms} confirmed, {result.appends} appended, "
            f"{result.disputes} disputed"
        )
    else:
        raise click.ClickException("provide an article ID or use --all")
```

- [ ] **Step 2: Verify CLI wiring**

Run: `uv run kebab agent research --help`
Expected: help text shown

- [ ] **Step 3: Type check**

Run: `uv run basedpyright app/cli.py`
Expected: 0 errors

---

### Task 8: Full Integration Test

**Files:** none (manual verification)

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest tests/ -q --tb=short -m "not expensive and not ai and not network"
```
Expected: all pass

- [ ] **Step 2: Lint and type check**

```bash
uv run ruff check . && uv run basedpyright app/
```
Expected: 0 errors

- [ ] **Step 3: Manual smoke test**

```bash
uv run kebab agent research SCI-EAR-TEC-001 --budget 3
```

Verify:
- External footnotes added to the article
- `research_claims_total` and `researched_at` in frontmatter
- New sources link to Wikipedia/OpenStax URLs

- [ ] **Step 4: Verify confidence update**

```bash
uv run kebab sync
uv run kebab list
```

Check that articles with >=70% confirmed claims and 0 disputes reach confidence 3.
