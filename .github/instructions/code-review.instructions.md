---
applyTo: "**"
---

# KEBAB Code Review Instructions

When reviewing pull requests, check for the following rules in priority order. Flag violations with confidence level (high/medium). Skip nitpicks and hypothetical concerns.

## Invariants (block the PR if broken)

- **17-field universal index.** No vertical ever adds fields to the Qdrant payload schema in `app/models/article.py`.
- **Markdown is the source of truth.** The Qdrant index is derived and can always be rebuilt from curated markdown.
- **Vertical-agnostic core.** `app/core/` never reads vertical-specific frontmatter fields. They pass through via `extra="allow"` on `FrontmatterSchema`.
- **No source, no save.** Content without a traceable source is discarded. Never invent content.
- **Cross-agent imports forbidden.** Sibling agents under `app/agents/` do not import from each other. They meet only via shared `app/core/` modules.

## Python & typing

- `from __future__ import annotations` in every module.
- Type hints on every function parameter and return value, including `-> None`.
- PEP 604 unions (`str | None`), not `Optional[str]`.
- `list[str]`, `dict[str, int]` over `typing.List`, `typing.Dict`.
- `Literal[...]` for closed string/int sets; `Protocol` for duck-typed interfaces.

## Code simplicity

- No edge-case handling for scenarios that can't happen. Validate only at system boundaries.
- No back-compat shims on internal functions — update all call sites in the same change.
- Prefer the simpler version. Readability beats cleverness.
- No speculative abstractions. Three similar lines is better than a premature helper.
- For `Literal`/`Enum` fields from LLM output, attach a `@field_validator(mode="before")` to normalize casing.

## Pydantic v2

- Every structured data type is a `BaseModel` with `Field(..., description="...")` on every field.
- `model_config = ConfigDict(...)`, not inner `class Config`.
- `extra="forbid"` for closed schemas; `extra="allow"` only for `FrontmatterSchema` and `ContextMapping`.
- `model_validate(dict)` and `model_dump()`, not v1 names (`.dict()`/`.parse_obj()`).
- Never shadow built-in type names as field names.

## pydantic-settings

- Single `Settings(BaseSettings)` in `app/config/config.py`. No `os.environ` reads in app code.
- Settings passed explicitly to stages and agents — never rely on the module-level singleton inside pipeline functions.
- Per-operation model settings (`ORGANIZE_MODEL`, `GENERATE_MODEL`, etc.) resolved via `resolve_model()` — never hard-coded model names.

## pydantic-ai agents

- One agent per directory in `app/agents/<name>/`. Main file named after the folder (`research/research.py`).
- Always declare `deps_type` and `output_type`.
- Deps are `@dataclass(kw_only=False)`, not Pydantic models.
- Prompts live in `app/agents/<name>/prompts/*.md`, not inlined beyond 2-3 lines.
- Every agent enforces the no-source-no-save invariant in its output model.
- Agents are sync-called (`agent.run_sync(...)`).

## Orchestrator design

- **No callable swap-points on `run()`.** Don't accept `planner=`, `searcher=`, `classifier=` parameters for testability. Inject at the per-step layer via `agent: Agent[...] | None = None` overrides or `monkeypatch.setattr` in tests.
- **No `mode=` flags** branching mostly-disjoint code paths. Split into sibling agents with separate CLI commands.
- **Pure plumbing goes in `core/`; semantics stay in `agents/`.** Plumbing = no LLM calls, no business rules, reused by 2+ agents. Semantics = agent-specific prompts and business rules.
- **One job per file.** Signal for splitting: a file has multiple top-level functions that don't call each other, or a docstring that uses "and".
- **State is read-only.** Pipeline stages return update dicts rather than mutating inputs.

## Markdown (AST-based)

- Body parsing uses `marko` (GFM + footnote plugin) via `parse_body()` / `render_body()`.
- `read_article(path)` returns `(fm, body, tree)` — callers that don't need the tree use `_, _, _`.
- Read helpers (`extract_section`, `extract_faq`, `extract_research_gaps`, etc.) accept `marko.Document`, not strings.
- Footnotes use structured `FootnoteDef` nodes (`number`, `title`, `url`, `source_id`), not regex.
- Writers manipulate the AST (paragraph walks, heading-level section boundaries, node insertion) — no regex for section boundaries or claim matching.
- `write_article(path, fm, body_str)` takes a rendered string — callers render the tree first.

## Frontmatter I/O

- Read with `frontmatter.load(path)`; write with `frontmatter.dumps(post)`.
- Always validate through `FrontmatterSchema.model_validate(...)`.
- Preserve unknown keys via `model_dump(exclude_none=False)`.

## HTTP clients

- Sync `httpx.Client` only. No async.
- Module-level or lazy-initialized client with explicit timeout and `User-Agent` header. No bare `httpx.get()` calls — they create and tear down a connection pool per request.

## Error handling

- Raise specific exceptions (`KebabError` subclasses), not bare `Exception`.
- `raise ... from original` to preserve chains.
- Catch narrowly. No bare `except:` or `except Exception:` without re-raising or at minimum `logger.warning(...)`.
- Pipeline stages are idempotent and resumable.

## Logging

- `logger = logging.getLogger(__name__)` at the top of every module.
- No `print` in library code. CLI uses `click.echo`.
- Never log secrets, raw prompts, or full source documents.

## Module layout

- Absolute imports only (`from app.models import Article`).
- No circular imports.
- Each agent directory: main file named after folder, optional `prompts/`, helper modules.
- Python package directories use `snake_case` (`research_gaps/`); CLI commands use `kebab-case` (`research-gaps`).

## Testing

- Test structure mirrors `app/`: `tests/unit/agents/research/`, `tests/unit/core/`, etc.
- Test naming: `test_<behavior>_when_<condition>`.
- Use `pytest-mock`'s `mocker` / `monkeypatch` — not `unittest.mock` directly.
- Never fail a test because the network or an API is down — `pytest.skip`.
- `@pytest.mark.expensive` for real LLM calls; never runs in CI by default.
- New pydantic model: add a unit test. New agent: add unit + integration tests. Bug fix: add a regression test.

## Naming

- `snake_case` for variables, functions, fields, module files.
- `PascalCase` for classes and type aliases.
- `SCREAMING_SNAKE` for module-level constants and Settings fields.
- `kebab-case` for CLI commands.
- Descriptive names. No abbreviations unless common in the domain.

## Commit discipline

- Small, focused commits. Conventional prefix: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
- Messages explain **why**, not what.
- Never commit: `.env`, `knowledge/.qdrant/`, `logs/`, `evals/results/`, `.venv/`.
- Run `uv run pytest -q && uv run ruff check . && uv run basedpyright app/` before every commit.

## What to skip in reviews

- Style preferences not codified above.
- "Could be more abstract" suggestions.
- Hypothetical edge cases that can't happen given internal data contracts.
- Findings below 70% confidence.
