# KEBAB

**Knowledge Engine for Building Authoritative Bases.**

CLI-first, domain-agnostic knowledge base curator. Markdown files are the source of truth; Qdrant holds the search index. Same tooling works for education, legal, healthcare, corporate, and product docs — vertical-specific metadata lives in markdown frontmatter, not the index or the code.

## Quickstart

```bash
uv venv
uv sync
uv run kebab --help
```

## Pipeline

```
ingest → organize → crawl → gaps → generate → contexts → verify → sync
```

Plus continuous agents: `qa` (enriches `## Q&A`) and `lint` (health checks).

See `CLAUDE.md` for house-style rules and `~/Downloads/kebab-knowledge-base-architecture.html` / `~/Downloads/kebab-technical-architecture_1.html` for the full spec.
