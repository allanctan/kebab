# KEBAB build progress

Tracks milestones from `~/.claude/plans/mutable-pondering-summit.md`
(original build, M0–M16) and `~/.claude/plans/source-gathering.md`
(source-adapter foundation, M17–M21).

## Original build (M0–M16)

- [x] M0  — Cross-project rule reconciliation & utilities
- [x] M1  — Settings expansion, LLM model resolution & presets (models.yaml + lazy expansion)
- [x] M2  — Confidence computation
- [x] M3  — Qdrant store wrapper
- [x] M4  — Embeddings
- [x] M5  — Sync stage
- [x] M6  — Search / check / tree / status CLI
- [x] M7  — Ingest (Stage 0)
- [x] M8  — Organize (Stage 1)
- [x] M9  — Crawl & Gaps (Stages 2–3)  *(crawl stage later collapsed into organize — see M16.2)*
- [x] M10 — Generate (Stage 4)
- [x] M11 — Contexts (Stage 5)
- [x] M12 — Verify (Stage 6)
- [x] M13 — Q&A agent
- [x] M14 — Lint agent
- [x] M15 — Evals
- [x] M16 — Pilot end-to-end (stubbed walkthrough verified; real Gemini run produced 14 articles in curated/Science at 3 × c3, 11 × c2)

## Post-pilot improvements (M16.x)

Landed in-session after M16 as the pipeline was exercised against real DepEd corpus material.

- [x] **M16.1 — Real-corpus pilot execution.** Live Gemini calls against 13 DepEd PDFs (grade 9 + grade 10 science/math/english). Surfaced 7 real bugs (stale embedding model default, pydantic-ai credential contract, eval-baseline mixing of adversarial + known-good, thin calibration datasets, parallel curated hierarchies, missing parent_ids, test fixture for `_FakeModels.embed_content`).
- [x] **M16.2 — Medallion layout refactor.** Introduced `raw/` → `processed/` → `curated/` three-tier layout with per-source folders under `processed/documents/<stem>/`. Dropped the `crawl` stage (it duplicated `organize`'s LLM call with a different prompt, producing conflicting IDs). `organize` now owns the canonical `plan.json` at `knowledge/.kebab/plan.json`; `gaps` reads the plan directly instead of a separate crawl file.
- [x] **M16.3 — Organize idempotency + `--force`.** Re-running `kebab organize` loads the cached plan and re-materializes missing stubs without calling the LLM. `--force` re-proposes from scratch. Each article node gets a reserved `md_path` that `generate` writes to directly, eliminating the path-drift bug that produced parallel trees.
- [x] **M16.4 — Multimodal PDF ingest.** `extract()` now pulls every figure (bytes, rect, content hash), `describe_image()` calls `gemini-2.5-flash-lite` per figure with a distinct labeler prompt, captions are inlined into `text.md` as `[Figure pN.M: ...]` markers. Anti-patterns avoided: silent YAML-like fallbacks, placeholder-injected prompts.
- [x] **M16.5 — Figure filter pipeline.** Four deterministic pre-LLM filter rules implemented in `app/core/figure_filters.py`:
  1. `tiny` — `rel_area < FIGURE_MIN_REL_AREA` (default 0.5% of page area)
  2. `solid_color` — dominant color usage ≥ `FIGURE_SOLID_COLOR_THRESHOLD` (default 0.99) via `pymupdf.Pixmap.color_topusage`
  3. `repeated` — SHA256 content hash on ≥ `FIGURE_REPEAT_PAGE_THRESHOLD` pages of the same document (default 3)
  4. `ribbon` — aspect ≥ `FIGURE_RIBBON_ASPECT` (default 10) AND `rel_area < FIGURE_RIBBON_MAX_REL_AREA` (default 5%)
  Thresholds tuned empirically against 543 human-sorted images. Result: **78.3% of figures filtered before any LLM call** on the DepEd corpus (947/1209 dropped), with near-perfect precision on solid_color (1.000) and tiny (0.992 after human review).
- [x] **M16.6 — figure_filter eval suite.** `kebab eval figure-filter` scores the algorithmic filter against a human-reviewed label set using precision/recall/F1 with `decorative` as the positive class. Dataset builder (`evals/datasets/figure_filter/build.py`) runs a distinct `PedagogicalJudge` agent over every raw figure and produces `labels.yaml` with `label`, `reasoning`, `confidence`, `reviewed` fields. Sort/review helpers (`sort.py` with `--mode label|disputed`) produce `useful/` + `decorative/` and `review_fp/` + `review_fn/` directories for visual human review.
- [x] **M16.7 — Describer retry + error recovery.** `describe_image` now retries transient failures (503, 429, 5xx) with exponential backoff (1s → 2s → 4s → 8s, max 4 attempts). Permanent errors (400-class) raise on first attempt. Describer failures are stamped with `skip_reason="describer_error"` and `description="ERROR: <msg>"` — **distinct from `DECORATIVE`** so they don't silently disappear into the decorative bucket. Image bytes are preserved on disk for error records. New `kebab ingest retry-errors --stem <stem>` CLI re-feeds only the failed figures without re-extracting the PDF.
- [x] **M16.8 — `FigureRecord` full provenance.** Every extracted figure now carries `rect_width`, `rect_height`, `page_width`, `page_height`, `rel_area`, `aspect`, `dominant_color_usage`, `content_hash`, `skip_reason` in `figures.json`. Operators can audit any filter decision offline without re-extracting from the PDF.
- [x] **M16.9 — `kebab ingest pdf --force`.** CLI flag to re-process PDFs even when their processed/ output already exists. Needed after any filter threshold or describer prompt change.
- [x] **M16.10 — Full corpus re-ingest verified.** Fresh end-to-end run: 13 PDFs, 1209 figures, **947 filtered pre-LLM (78.3%)**, 262 Gemini describer calls, **0 labeler errors** (retry/backoff held up through rate-limit spikes). `figures.json` per-doc audit trail verified complete.
- [x] **M16.11 — Incremental organize (cases 1/2/3).** When new sources appear under `processed/documents/`, `organize` now extends the cached plan non-destructively: runs a second LLM call with the *existing tree + new manifest entries only*, asking whether each new source extends an existing article or warrants a new one. Merge logic (`_merge_plans`) unions `source_files` on existing IDs and appends net-new nodes, refusing to rename, re-parent, or drop anything already in the plan. `gaps` detects staleness by diffing each curated article's frontmatter `source_stems` against the plan's `source_files`; stale gaps feed back into `generate`, which stamps `source_stems` and `parent_ids` into frontmatter on every write and preserves existing `verifications` / `human_verified_*` fields across regens. Covered by 10 unit tests (`tests/unit/pipeline/test_organize_merge.py`) + 5 new integration tests spanning organize/gaps/generate.

## Source-gathering milestones (M17–M21)

Plan: `~/.claude/plans/source-gathering.md`. Adds four new acquisition channels plus an autonomous research agent on top of a shared adapter foundation.

- [x] M17 — Source-adapter foundation (protocol, fetcher, provenance, adapter wrappers) — **partial**: foundation layer (`source_adapter.py`, `provenance.py`, `fetcher.py`, `registry.py`, `adapters/local_pdf.py`, `adapters/local_dataset.py`, `adapters/direct_url.py`) + `Source` envelope enrichment + 3 new `Settings` fields + 33 unit tests all landed. **Deferred**: CLI rewiring (`kebab ingest pdf|csv|web` still calls the legacy ingest functions directly; wiring through the registry is a behavior-neutral follow-up that can land alongside M18).
- [x] M18 — Tavily search adapter. `TavilyAdapter` wraps `tavily-python` SDK. `discover()` searches, `fetch()` downloads via `SharedFetcher`. Default tier 4. Requires `TAVILY_API_KEY`. 9 unit tests + 1 network test.
- [x] M19 — Wikipedia adapter. `WikipediaAdapter` uses MediaWiki REST API (opensearch + extracts). No API key needed. CC-BY-SA-3.0 license. Redirect handling via `&redirects=true`. 13 unit tests + 1 network test.
- [x] M20 — OpenStax adapter. `OpenStaxAdapter` searches OpenStax CMS API for books. Default tier 2 (peer-reviewed), CC-BY-4.0. Discovery-only — book-level search, not section content. 13 unit tests + 1 network test.
- [x] M21 — Research agent. Two-stage architecture: planner (extracts claims, generates search queries) → executor (searches adapters, classifies findings as confirm/append/dispute, judges disputes). Replaces the verify stage. Confidence v2: ≥70% claims confirmed + 0 disputes → confidence 3. Articles enriched with Obsidian footnotes linking to external sources. `kebab agent research <id>` or `--all`.

## Post-M21 improvements

- [x] **Source index.** Deterministic integer IDs for all ingested sources. Registered at ingest time in `knowledge/.kebab/sources.json`. Source IDs flow through organize → gaps → generate. Replaces filename-stem-based tracking.
- [x] **Obsidian footnotes.** Generate stage produces `[^N]` citations with footnote definitions linking to raw PDFs. Research stage adds external footnotes linking to Wikipedia/web URLs. Deduped per-source-URL.
- [x] **Configurable path metadata extraction.** `SOURCE_PATH_PATTERN` in settings (e.g. `raw/documents/grade_{grade}/{subject}/{filename}`) extracts structured metadata from folder paths into the source index.
- [x] **Pluggable vertical contexts.** `EducationContext`, `HealthcareContext`, `PolicyContext`, `LegalContext` — each self-contained with `SYSTEM_PROMPT` and `VERTICAL_KEY` ClassVars. Selected via `CONTEXT_VERTICAL` setting.
- [x] **Multi-model support.** `models.yaml` registry with aliases: `gemini-flash`, `gemini-pro`, `gpt-5.4-mini` (Azure), `haiku-4.5`, `sonnet-4.6` (Bedrock), `minimax` (OpenAI-compat). Lazy credential expansion.
- [x] **Per-operation model settings.** 9 settings (`ORGANIZE_MODEL`, `GENERATE_MODEL`, `CONTEXTS_MODEL`, `RESEARCH_PLANNER_MODEL`, `RESEARCH_EXECUTOR_MODEL`, `RESEARCH_JUDGE_MODEL`, `QA_MODEL`, `LINT_MODEL`, `FIGURE_MODEL`) — each independently configurable via `.env`.
- [x] **LLM trace logging.** All pydantic-ai agent calls traced to `logs/llm-trace-YYYY-MM-DD.jsonl` via OpenTelemetry span exporter. Full input/output/timing captured. Works with or without Logfire token.
- [x] **Secrets separation.** `.env` (committed) holds config. `.env.local` (gitignored) holds secrets. Dropped `KEBAB_` env prefix.
- [x] **Verify stage replaced by research agent.** Old multi-LLM same-source verification removed. Research agent checks claims against independent external sources (Wikipedia, Tavily).

## Open follow-ups (not in any plan yet)

- [x] ~~**Incremental organize (cases 1/2/3).**~~ Landed as M16.11.
- [x] ~~**`parent_ids` propagation in generate.**~~ Landed as part of M16.11 — `generate.py` now stamps `parent_ids` (and `source_stems`) on every write.
- [ ] **Incremental eval dataset labeling.** `build.py` in `evals/datasets/figure_filter/` is incremental (idempotent), but hit Gemini rate limits when running the full corpus. The run has only covered 9 of 13 docs (543 of 1209 figures) — re-run against the remaining 4 when labeling the final baseline.
- [ ] **pydantic-evals adoption decision.** Dep is in `pyproject.toml` + CLAUDE.md §20 but we use raw dataclass scorers in `evals/evaluators/`. Either adopt `pydantic_evals.Dataset`/`Evaluator` everywhere or drop the dep.
- [ ] **Lint vertical checks.** `app/agents/lint/agent.py` is missing the three vertical-aware checks from the spec: healthcare `review_by` freshness, legal `valid_until` expiry, corporate `policy_version` currency.
- [ ] **Generate prompt section template.** Spec §5 calls for `## Core concepts / ## Key facets / ## Common misconceptions / ## Q&A`. `app/pipeline/prompts/generate_system.md` only requires a `# {topic_name}` heading. Q&A agent also omits the spec's `Source:` line per pair (sources land in frontmatter instead).
