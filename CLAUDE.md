# KEBAB — House Rules

Rules for contributors (human and AI) working on this repo. Organized around the actual stack we use.

For an end-to-end developer walkthrough of the pipeline (ingest → research),
see [`docs/pipeline.md`](docs/pipeline.md). This file is the *house rules*;
that file is the *tour*.

## 1. Invariants (never break these)

- **12-field universal index.** The Qdrant payload schema in `app/models/article.py` is the same for every vertical. No vertical ever adds fields to the index.
- **Markdown is the source of truth.** The Qdrant index is derived during `kebab sync` and can always be rebuilt from markdown.
- **Vertical-agnostic core.** KEBAB never reads vertical-specific frontmatter fields (`bloom_ceiling`, `evidence_grade`, `policy_version`). They pass through via `model_config = ConfigDict(extra="allow")`.
- **No source, no save.** Content without a traceable source is discarded at ingest, generate, and Q&A stages. Never invent content.

## 2. Stack

Python 3.11+ · `uv` · `click` · `pydantic` v2 · `pydantic-settings` · `pydantic-ai` · `python-frontmatter` · `pymupdf` · `httpx` · `beautifulsoup4` · `tiktoken` · `qdrant-client` · `gitpython` · `pytest` · `pydantic-evals` · `ruff` · `basedpyright`. No FastAPI, no Celery — CLI-first and sync.

## 3. Python & typing

- Target Python ≥3.11. Use PEP 604 unions (`str | None`), not `Optional[str]`.
- Type hints on **every** function parameter and return value, including `-> None`.
- **Add `from __future__ import annotations` to every new module.**

### 3b. Code simplicity

- Don't handle edge cases that won't happen. Trust internal data contracts; validate only at system boundaries.
- No back-compat shims on internal functions. When an internal signature changes, update **all** call sites in the same change.
- Prefer the simpler version. Readability beats cleverness.

### 3c. Normalizing validators for LLM output

- For `Literal`/`Enum` fields populated from LLM responses, attach a `@field_validator(mode="before")` that lowercases and snake_cases the input. LLMs return `"Correct"`, `"REMEMBER"`, `"Build Up"`; normalize to canonical form before validation.

### 3d. Enums as single source of truth

- Any closed set (`LevelType`, `SourceTier`, `ConfidenceLevel`, …) is defined once. No inline string literals in pipeline code, no duplicate dicts.

### 3e. AliasChoices for external data

- When consuming data from sources that may use either `snake_case` or `camelCase`, declare fields as `snake_case` with `validation_alias=AliasChoices("snake_case", "camelCase")`.

### 3f. lru_cache expensive parsers

- Wrap pure, deterministic parsers (e.g. path-keyed `parse_article`) with `@lru_cache` so repeat reads are free.
- Run `uv run basedpyright app/` and keep it clean (standard mode). Fix types, don't `# type: ignore` them.
- Prefer `list[str]`, `dict[str, int]` over `typing.List`, `typing.Dict`.
- Use `Literal[...]` for closed enums of strings/ints (e.g. `LevelType`, `SourceTier`).
- Use `Protocol` for duck-typed interfaces; `ABC` only when subclassing is mandatory.

## 4. Naming

- `snake_case` — variables, functions, fields, module files.
- `PascalCase` — classes and TypeAliases.
- `SCREAMING_SNAKE` — module-level constants and Settings fields.
- `kebab-case` — CLI commands, top-level directories.
- Descriptive. No abbreviations unless already common in the domain.

## 5. Project & dependencies (`uv`)

- **All commands go through `uv run`.** Never activate `.venv` manually; `uv run` does the right thing.
- Add runtime deps with `uv add <pkg>`; dev deps with `uv add --dev <pkg>`.
- `uv.lock` is committed. Do not hand-edit it.
- Pin floors with `>=`, not exact versions. Let `uv.lock` freeze the resolution.
- One virtualenv per repo (`.venv/`), gitignored.

## 6. Pydantic v2

- Every structured data type is a `BaseModel`.
- Every field declares `Field(..., description="…")`. Descriptions are user-facing docs for the LLM tooling, not decoration.
- Configure via `model_config = ConfigDict(...)`, not inner `class Config`.
- `extra="forbid"` for closed schemas like `Article`.
- `extra="allow"` **only** for `FrontmatterSchema` and `ContextMapping` — they must pass vertical-specific keys through untouched.
- Validate at system boundaries (file read, Qdrant read, CLI input). Trust models internally.
- Prefer `model_validate(dict)` and `model_dump()` over `.dict()`/`.parse_obj()` (v1 names).
- Never shadow built-in type names as field names. If you must (e.g. `date`), alias the import: `from datetime import date as _date`.
- Use `field_validator` / `model_validator` only for cross-field invariants that can't be expressed in the type.

## 7. pydantic-settings (config)

- Single `Settings(BaseSettings)` in `app/config/config.py`.
- No env prefix. Load `.env` for config, `.env.local` for secrets via `load_dotenv()` calls in `config.py`.
- Mandatory fields: `Field(default=...)` — fails fast at import time when missing.
- Accessed via `from app.config import env` (module-level `env = get_settings()` cached with `@lru_cache`).
- Never read `os.environ` directly in app code. If a new knob is needed, add it to `Settings`.
- Settings is **passed explicitly** to pipeline stages and agents — don't rely on the module-level singleton inside those functions; take `settings: Settings` as a parameter for testability.
- **Per-operation model settings.** Each LLM operation has its own setting (`ORGANIZE_MODEL`, `GENERATE_MODEL`, `CONTEXTS_MODEL`, `RESEARCH_PLANNER_MODEL`, `RESEARCH_EXECUTOR_MODEL`, `RESEARCH_JUDGE_MODEL`, `QA_MODEL`, `LINT_MODEL`, `FIGURE_MODEL`). All default to `gemini-flash`. Set to any alias from `app/config/models.yaml` or a `provider:model` string.
- **Model aliases** are defined in `app/config/models.yaml` and resolved via `app/core/model_presets.py`. `${VAR}` references in YAML entries are lazy-expanded — missing credentials for unused aliases don't break startup. Supported providers: `google-gla`, `openai`, `anthropic`, `openai-compat` (Azure, MiniMax), `bedrock` (AWS Claude).
- **Secrets** go in `.env.local` (gitignored). Config goes in `.env`. Both loaded via `load_dotenv()`.

## 8. Click (CLI)

- One `@click.group()` root in `app/cli.py`; nested groups for `ingest`, `agent`, `eval`.
- Use `click.echo` (not `print`) for CLI output.
- Use `click.Path(exists=True, dir_okay=..., file_okay=..., path_type=Path)` for path inputs — never raw `str`.
- Declare commands with `kebab-case` names via `@group.command("name")`; function names stay `snake_case`.
- Side-effect-free imports: `main()` calls `setup_logging()` once on the group, not at module load.
- Long-running commands must print progress. Use `click.progressbar` for known iteration counts.
- Exit codes: 0 success, 1 handled failure, 2 usage error. Use `click.ClickException` for user-facing errors.

## 9. pydantic-ai (agents)

- One agent per directory in `app/agents/<name>/`. No mega-agents. Agent directory names are `kebab-case` (e.g. `qa-enrichment`, `lint-checker`). When we introduce skills, they follow `kebab-case-skill` inside `app/agents/<name>/skills/`.
- Steps, fields, and Python identifiers remain `snake_case`.
- Always declare `deps_type` and `output_type` — no free-form string agents.
- Deps are `@dataclass(kw_only=False)` (not Pydantic) — they carry runtime context, not serialized state.
- Tools are module-level functions decorated with `@agent.tool`, typed `(ctx: RunContext[Deps], ...) -> T`.
- Model identifier comes from per-operation settings (e.g. `settings.QA_MODEL`, `settings.RESEARCH_PLANNER_MODEL`) — never hard-coded in agent code. Resolved via `app.core.llm.resolve_model()` which handles aliases and `$VAR` expansion.
- Prompts live in `app/agents/<name>/prompts/*.md` loaded at module import, not inlined beyond 2–3 lines. The prompt file is the agent's "Instructions section" — detailed, deterministic, with clear Input/Output contracts documented at the top.
- Every agent must enforce the **no-source-no-save** invariant in its output model (e.g. `sources: list[Source]` with `min_length=1`).
- Agents are **sync-called from sync pipeline code**. If the agent is async, wrap with `agent.run_sync(...)`.

### Input / Output contracts

Every agent's prompt includes explicit `## Input` and `## Output` sections documenting each field with a description:

```markdown
## Input
- `article_id`: ID of the article to enrich
- `existing_questions`: List of questions already present

## Output
- `new_questions`: List of new grounded questions
- `sources`: List of source citations for each answer
- `is_ready_to_commit`: Boolean flag
```

This documentation is for humans, but we also use it as the prompt the LLM sees.

### State is read-only

When an agent or pipeline stage needs to update shared state, it **returns a dict of updates** rather than mutating inputs:

```python
def update_state(state: QaState, output: QaResult) -> dict:
    """Read from state; return updates. Never mutate."""
    return {
        "questions_added": state.questions_added + len(output.new_questions),
        "last_run_at": output.completed_at,
    }
```

### Orchestrator design (lessons from the 2026-04-12 research restructure)

These rules come from refactoring a 469-line `research/agent.py` that
mixed claim verification, gap answering, and image enrichment with mode
flags and callable swap-points. Apply them to every new orchestrator:

- **No callable swap-points on `run()` for testability.** Don't take
  parameters like `planner=plan_research`, `searcher=_default_searcher`,
  `classifier=classify_finding` on the orchestrator's `run()` so tests
  can pass stubs. Inject at the per-step layer instead — each LLM step
  function takes `agent: Agent[...] | None = None` (same pattern as
  `planner.plan_research`), and pure functions are mocked via
  `monkeypatch.setattr(orchestrator_module, "search", stub)`. The big
  callable bag pattern produces awkward gating like
  `if classifier is classify_finding: do_real_thing()`.
- **No `mode="all"|"foo"|"bar"` flags branching the orchestrator** when
  the branches don't share most of their work. Three modes that each
  hit different code paths means three sibling agents in three sibling
  directories with three CLI commands. Mode flags are usually three
  agents fighting in a trench coat.
- **Cross-agent imports are forbidden between sibling agents.**
  `agents/research_gaps/` does not import from `agents/research/`. They
  meet only via shared `core/` modules. This invariant is what lets a
  future supervisor agent invoke any agent in any order without
  worrying about hidden coupling.
- **Pure plumbing → `core/`; semantics → `agents/`.** A function is
  "pure plumbing" if it has no LLM calls and no business rules — it
  just dispatches, formats, or transports data. Plumbing reused by 2+
  agents gets promoted to `app/core/<topic>/`. Functions with prompts
  or business rules stay agent-local even if they look similar across
  agents — different prompts mean different code.
- **One job per file inside an agent.** When `verifier.py` does both
  classification AND markdown rewriting, split into `verifier.py` and
  `writer.py`. The signal: a file has multiple top-level functions that
  don't call each other, or a docstring that uses "and".
- **Drop vestigial counters and dead state.** When you spot a variable
  that's set but never read in the rendered output (the
  `local_num=fig_num` field that no consumer reads), delete it as part
  of the lift. This is "free cleanup" — out of scope only if you have
  to add new code to enable the deletion.

### Refactor migration order

When restructuring an existing agent, follow this order so the test
suite stays green at every step:

1. Add the new shared core module (e.g. `core/research/searcher.py`)
   alongside the old code. Do not delete anything yet.
2. Add tests for the new module.
3. Forward the old code's helpers to the new module (one-line shims) so
   the existing orchestrator now uses the new core under the hood.
4. Split file responsibilities (e.g. `executor.py` → `verifier.py` +
   `writer.py`) by renaming and creating new files. Keep the old
   orchestrator importing the new locations.
5. Add the *new* sibling agents (e.g. `research_gaps/`,
   `research_images/`) by lifting code from the *still-existing* old
   orchestrator. Don't delete the old one yet — it's the source of truth
   you're lifting from.
6. Replace the old orchestrator with its slimmed-down successor and
   delete the old file. Update the CLI to import the new entry point.
7. Migrate tests to their new homes.

The principle: never delete the source-of-truth file before its code
has been lifted into all of its new homes. If you do, you have to fish
the code back out of git history, which is slow and error-prone.

### Layering exceptions are documented at the import site

If a module *must* break a layering rule (e.g. `core/research/searcher`
imports from `agents/ingest/registry` to resolve adapter names), the
exception:

- Gets a TODO comment at the import site explaining why and what would
  fix it long-term.
- Gets a note in the design spec listing the tolerated exception
  explicitly.
- Is the only exception of its kind. If a second module wants to break
  the same rule, that's the signal to actually fix the underlying
  layering instead of growing the exception list.

Pretending exceptions don't exist is a lie that bites the next
contributor.

### Linear workflows preferred

Prefer linear stage sequences over conditional branching. Use scripts/conditionals only when necessary. Each agent/stage does one thing well (Single Responsibility).

```python
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext

from app.core.llm.resolve import resolve_model

@dataclass
class QaDeps:
    settings: Settings
    article_id: str

qa_agent = Agent(
    model=resolve_model(settings.QA_MODEL),
    deps_type=QaDeps,
    output_type=QaResult,
    system_prompt=(PROMPTS_DIR / "qa_system.md").read_text(),
)

@qa_agent.tool
def lookup_source(ctx: RunContext[QaDeps], source_id: str) -> str:
    ...
```

## 10. python-frontmatter (markdown I/O)

- Read with `frontmatter.load(path)`; write with `frontmatter.dumps(post)`.
- **Always** validate `post.metadata` through `FrontmatterSchema.model_validate(...)` before use.
- Preserve unknown keys on write: dump via `post.metadata = schema.model_dump(exclude_none=False)` — `extra="allow"` keeps them.
- YAML dates/datetimes round-trip as `datetime.date`/`datetime` — don't convert to strings.
- Extract FAQ questions with a dedicated `extract_faq(body)` helper; never regex-sprinkle through the codebase.

## 11. Qdrant (`qdrant-client`)

- One wrapper: `app/core/store.py::Store`. All stages talk to Qdrant through it — no direct `QdrantClient` imports elsewhere.
- `Store.__init__` picks local (`QdrantClient(path=...)`) vs server (`QdrantClient(url=...)`) based on `settings.QDRANT_URL` precedence.
- Use named vectors only if we add hybrid search later; for now, single unnamed dense vector.
- Payload keys are **flat snake_case** matching `Article` fields. Nested JSON only for `contexts`.
- Use `query_points` (not deprecated `search`).
- Always filter with `Filter(must=[FieldCondition(...)])` — never client-side filter after retrieval.
- Collection name from `settings.QDRANT_COLLECTION`; `ensure_collection()` is idempotent and called at sync startup.
- Integration tests use `QdrantClient(":memory:")` — fast, no cleanup.

## 12. pymupdf (PDF extraction)

- Import as `import pymupdf` (not the legacy `fitz` alias, though it still works).
- Use `with pymupdf.open(path) as doc:` — always a context manager, always close.
- Iterate pages with `for page in doc:`, extract with `page.get_text("text")` for prose, `"blocks"` for structured.
- Reject encrypted PDFs with a clear error rather than silently extracting nothing.
- PDF extraction is sync and CPU-bound — fine in pipeline stages.

## 13. httpx + BeautifulSoup (web scraping)

- Sync `httpx.Client` only. No async in KEBAB.
- Use a module-level `Client` with timeout (`httpx.Client(timeout=30.0, follow_redirects=True)`).
- Set a descriptive `User-Agent: kebab/<version> (+contact)`.
- Parse with `BeautifulSoup(html, "html.parser")` — no `lxml` dependency.
- Extract text via `.get_text(separator="\n", strip=True)`, then normalize whitespace.
- Respect `robots.txt` and rate limits. Any scraper must cache the raw HTML under `knowledge/raw/` before extraction so we never re-fetch.

## 14. tiktoken (token counting)

- Use `tiktoken.encoding_for_model(model)` with the configured curation model name.
- Cache encoders at module level — they're expensive to construct.
- Enforce `settings.MAX_TOKENS_PER_ARTICLE` (default 50k) at generate-time **and** at sync-time. Lint agent flags violations.

## 15. gitpython (git operations)

- Only for agent-driven commits (Q&A enrichment, lint fixes). Never for user-facing git.
- Open repos with `git.Repo(path)` inside a function — don't hold Repo objects across calls.
- Always commit with an explicit author string like `"KEBAB Q&A Agent <agent@kebab.local>"`.
- Never `push`, never touch remotes — that's the operator's job.

## 16. Logging

- `logger = logging.getLogger(__name__)` at the top of every module.
- **No `print`** in library code. CLI entrypoints use `click.echo` for stdout, `logger.info` for side information.
- Configure once via `setup_logging()` from `app.config`. Idempotent — safe to call multiple times.
- Log levels: `DEBUG` for traces, `INFO` for stage progress, `WARNING` for recoverable issues, `ERROR` for failures the user must see.
- Never log secrets, raw prompts, or full source documents.
- **Logfire** is enabled via `setup_logging()`: `instrument_pydantic_ai()` traces agent runs end-to-end, `instrument_httpx()` captures outbound calls. Runs local-only when `KEBAB_LOGFIRE_TOKEN` is unset — no cloud egress required.

## 17. Module layout

- Every package has `__init__.py`. Public packages declare explicit `__all__` with re-exports.
- Absolute imports only: `from app.models import Article`. Never `from .models import Article`.
- **Each agent is a directory** in `app/agents/<name>/` with a **main file named after the folder** (e.g. `organize/organize.py`, `generate/generate.py`) containing the primary `run()` function. Optional: `prompts/`, helper modules.
- No circular imports. If you need one, you have a layering bug.

### Top-level structure

```
app/
  cli.py        # Click CLI root
  config/       # Settings, logging, model aliases
  core/         # Reusable infrastructure — no agents, no orchestration
    errors.py, markdown.py, store.py, confidence.py
    llm/        # Model resolution, embeddings, token counting, tracing
    images/     # Multimodal describer, deterministic figure filters, figure manifest
    sources/    # SourceAdapter protocol, source index, fetcher, provenance
    research/   # Shared plumbing for research-* agents (no LLM)
  models/       # Pydantic data models — no I/O, no business logic
  agents/       # All pipeline stages + autonomous agents
    ingest/         # PDF + web + adapters + registry
    organize/       # Hierarchy planner; produces plan.json + stubs
    generate/       # Contexts → gaps → write articles (one orchestrator)
    research/       # Claim verification (planner + verifier + writer)
    research_gaps/  # Standalone gap answering
    research_images/# Standalone Wikipedia image enrichment
    qa/             # Q&A pair enrichment + gap discovery
    lint/           # Health checks (no LLM)
    sync/           # Embed + upsert to Qdrant
  utils/        # Cross-cutting helpers
```

For per-file detail and the data shapes flowing between stages, see
[`docs/pipeline.md`](docs/pipeline.md). That file is updated when stages
change; this tree only needs maintenance when a top-level directory is
added or removed.

Note: Python package directories on disk use `snake_case`
(e.g. `research_gaps/`) because Python imports cannot contain hyphens.
The CLI command names use `kebab-case` (`kebab research-gaps`)
per rule §4. The two map 1:1 — the CLI command name is the directory
name with underscores replaced by hyphens.

## 18. Errors & control flow

- Raise specific exceptions, not bare `Exception`.
- Define a `KebabError` base class in `app/core/errors.py` when the first domain-specific error is needed; subclass for `ValidationError`, `SyncError`, etc.
- `raise KebabError(...) from original` to preserve chains.
- Catch narrowly. No bare `except:` or `except Exception:` without re-raising.
- Pipeline stages are **idempotent and resumable** — running a stage twice produces the same result.

## 19. pytest

### Layout

- `tests/unit/` — fast, no I/O beyond `tmp_path`, no network, no LLM calls. Directory structure **mirrors `app/`** (e.g. `tests/unit/models/`, `tests/unit/core/`, `tests/unit/agents/research/`).
- `tests/integration/` — real file I/O in `tmp_path`, in-memory Qdrant (`QdrantClient(":memory:")`), still no network.
- `tests/fixtures/` — shared test data (example frontmatter, small PDFs, minimal knowledge trees). Reusable across suites.
- `tests/conftest.py` — global fixtures: `knowledge_dir`, `mock_env`, `track_latency`.
- Closer `conftest.py` files for subsystem-specific fixtures.

### pyproject.toml config (already applied)

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = ["-v", "--strict-markers", "--tb=short", "--disable-warnings"]
markers = [
    "unit", "integration", "slow",
    "expensive",  # real LLM / embedding API calls (cost money)
    "ai",         # real AI model calls
    "network",    # requires outbound network
]
```

### Markers

- `@pytest.mark.unit` — default for `tests/unit/`.
- `@pytest.mark.integration` — default for `tests/integration/`.
- `@pytest.mark.slow` — anything >1s.
- `@pytest.mark.expensive` — real LLM / embedding calls. **Never runs in CI by default.**
- `@pytest.mark.ai` — any real model call (broader than `expensive`).
- `@pytest.mark.network` — requires outbound HTTP.
- Always `--strict-markers` — undeclared markers fail the run.

### Performance budgets

| Tier | Budget | Runs in CI? |
|------|--------|------------|
| Unit | < 1s per test | Yes, every push |
| Integration (mocked/in-memory) | < 2s per test | Yes, every push |
| LLM / expensive | 5–30s per test | Only on demand (`-m expensive`) |

### Naming

- Test classes: `Test<Feature>` grouped by functionality.
- Test methods: `test_<behavior>_when_<condition>` — behavior first.
- Descriptive over clever. `test_extract_faq_returns_empty_list_when_no_qa_section` beats `test_faq_edge_case`.

### Fixtures

- Use `pytest-mock`'s `mocker` fixture over raw `unittest.mock`.
- Standard fixtures provided in `tests/conftest.py`:
  - `knowledge_dir` — isolated `tmp_path/knowledge/` with `raw/{documents,datasets}/` pre-created.
  - `mock_env` — monkeypatched `Settings` with stub model names.
  - `track_latency` — context manager that prints operation duration.
- Add subsystem fixtures in closer `conftest.py` (e.g. `tests/unit/agents/research/conftest.py`).

### Network & LLM failures

**Never fail a test because the network or an API is down.** Catch and skip:

```python
try:
    result = qa_agent.run_sync(deps=deps)
except (httpx.ConnectError, httpx.TimeoutException) as e:
    pytest.skip(f"LLM API not available: {e}")
```

Event-loop isolation isn't an issue for KEBAB (sync-only), but the skip pattern still applies to any external dependency.

### Coverage

- Target: **>90%** for `app/core/` and `app/models/` (the load-bearing foundations).
- Target: **>80%** overall.
- Run: `uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=80`.

### Commands

```bash
# Fast feedback (default, what CI runs)
uv run pytest -m "not expensive and not ai and not network"

# Unit only
uv run pytest tests/unit

# Integration only
uv run pytest tests/integration

# Full LLM suite (costs money)
uv run pytest -m expensive

# Coverage
uv run pytest --cov=app --cov-report=html
```

### When to add a test

- New Pydantic model → unit test instantiating it with a spec example.
- New pipeline stage → integration test against a `tmp_path` knowledge tree with in-memory Qdrant.
- New agent → unit test with mocked `Agent.run_sync`, integration test with `@pytest.mark.expensive` for real calls.
- Bug fix → regression test that fails before the fix and passes after.

## 20. pydantic-evals

Evals test **LLM output quality** — separate discipline from pytest, which tests code correctness.

### Layout

```
evals/
├── datasets/        # Input cases, JSON/YAML
├── evaluators/      # LLM-as-judge evaluators
├── suites/          # One file per suite
├── tasks/           # Reusable eval tasks
└── results/         # Timestamped run outputs (gitignored)
```

### Suites

Three canonical suites, each invoked by `kebab eval <name>`:

| Suite | Question | Method |
|-------|----------|--------|
| `generation` | Is generated content grounded in cited sources? | LLM-as-judge over `(article, sources)` |
| `verification` | Does the verifier catch injected errors? | Inject faults, measure detection rate |
| `qa` | Are Q&A pairs grounded **and** useful? | LLM-as-judge on `(question, answer, source)` |

### Rules

- **Evals never block CI by default.** They cost money and have variance. Run on demand or on a schedule.
- **Use `pydantic-evals` Evaluators**, not hand-rolled scoring.
- **Datasets are versioned.** `evals/datasets/generation_v1.json`. Bump the version, don't mutate.
- **Results are timestamped JSON** under `evals/results/<suite>/<YYYY-MM-DD_HH-MM>.json`. Gitignored by default; commit only when bumping a baseline.
- **Baselines live in `evals/suites/<suite>_baseline.json`** — committed, reviewed via PR.
- **Regression rule**: any PR that lowers a baseline metric must update the baseline file and include justification in the commit message.
- **No network in unit tests** — eval tests of *evaluators themselves* go in `tests/unit/evals/` with mocked judge calls.
- **Cost budget**: document expected cost in the suite docstring (e.g. `"~$0.02 per run"`). Operators should know before running.
- **Mark slow evals** with `@pytest.mark.slow` if they're wrapped as pytest-callable.
- **Fixtures**: eval inputs reference real articles under `tests/fixtures/articles/` so datasets stay small.
- **Graceful API failure**: eval runs must not crash on transient API failures — retry once, then skip and report in the result JSON.

### Eval authoring checklist

1. Define the input dataset (JSON/YAML) in `evals/datasets/`.
2. Write the Evaluator in `evals/evaluators/` using `pydantic-evals`.
3. Wire them in a `evals/suites/<name>.py` file.
4. Run `kebab eval <name>` to produce a result file.
5. Review the output, bump baseline if intentional.
6. Document cost and runtime in the suite docstring.

## 21. Lint / format (`ruff`, `basedpyright`)

- `uv run ruff check .` and `uv run ruff format .` — zero warnings before commit.
- Ruff config lives in `pyproject.toml`. We opt in to rules, not out — start minimal.
- `uv run basedpyright app/` must be clean in standard mode.
- No `# noqa` without a reason comment. No `# type: ignore` without a linked issue.

## 22. Commit discipline

- Small, focused commits. Conventional prefix: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
- Commit messages explain **why**, not what — the diff shows the what.
- Never commit: `.env`, `knowledge/.qdrant/`, `logs/`, `evals/results/`, `.venv/`.
- Run `uv run pytest -q && uv run ruff check . && uv run basedpyright app/` before every commit.

## 23. Security & PII

- No secrets in code or frontmatter. All credentials through `Settings` → env vars.
- Don't log or commit raw source documents that may contain PII.
- LLM calls must not be sent credentials, user PII, or raw private data beyond the article being processed.

## References

- `~/Downloads/kebab-knowledge-base-architecture.html` — knowledge base architecture spec.
- `~/Downloads/kebab-technical-architecture_1.html` — technical architecture spec.
- [`docs/pipeline.md`](docs/pipeline.md) — developer walkthrough of the pipeline (ingest → research).
