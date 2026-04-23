# Local Source Gap Answering — Design Spec

**Date:** 2026-04-24
**Status:** Draft
**Branch:** `feature/content-expert`

## Summary

When answering research gaps, try the article's own source documents
first before searching the web. The LLM reads the processed source text
and answers the gap question from it. Answers are tagged with the model
name: `(Answer by gemini-flash, sourced from [Module Title])`.

If local sources don't answer the gap, fall back to external search
(authoritative domains → Wikipedia) as before.

## Problem

Research-gaps searches the web for gap answers, but many gap questions
are specific to the article's pedagogy or domain (e.g., "What is the
step-by-step procedure for the logs-in-a-pile problem?"). Web sources
rarely have these answers. But the original source PDFs — already
ingested and processed — often do.

For KNO-MAT-112 (Arithmetic Sequences), 7 gaps were discovered and 0
were answered by external search. The source modules likely contain
answers to most of them.

## Solution

### New flow for research-gaps

```
1. Extract unanswered gaps from article
2. Resolve article's source documents (frontmatter.sources → source index → processed stems)
3. Load processed text for each source
4. For each gap question:
   a. Try LOCAL SOURCES FIRST: pass gap question + all source texts to LLM
      → if answered, write Q/A block tagged with model name
   b. If not answered locally: fall back to EXTERNAL SEARCH (existing flow)
5. Write answers to article
```

### How local answering works

One LLM call per batch of gaps (not per gap):

```
System prompt: "You answer gap questions using ONLY the provided source
documents. If the source documents do not contain enough information to
answer a question, respond with is_answered=false."

User prompt:
  ## Source documents
  ### [Module 3: Arithmetic Sequences] (source_id: 4)
  <processed text, truncated to 8K tokens>

  ### [Module 1: Patterns and Sequences] (source_id: 3)
  <processed text>

  ## Gap questions
  0. "What is the second form of the sum formula?"
  1. "How do you find all inserted arithmetic means?"
  ...

Output: list[LocalGapAnswer]
```

### LocalGapAnswer model

```python
class LocalGapAnswer(BaseModel):
    gap_idx: int
    is_answered: bool
    answer: str              # empty if not answered
    source_title: str        # title of the source document used
    source_id: int           # source ID from the article's frontmatter
    reasoning: str
```

### Answer tagging

Gap answers from local sources are tagged differently from external
search answers:

```markdown
- **Q: What is the second form of the sum formula?**
  **A:** The second form is Sₙ = n/2(2a₁ + (n−1)d), useful when the
  last term is not known. (Answer by gemini-flash, sourced from
  [MATH GR10 QTR1-MODULE-3](../processed/...))
```

External search answers keep the existing format:
```markdown
- **Q: How frequently do magnetic reversals occur?**
  **A:** Every 450,000 years on average. (Source: [Geomagnetic reversal](https://en.wikipedia.org/wiki/...))
```

### Source text resolution

The article's frontmatter has a `sources` list with `id`, `title`, and
`checksum`. The source index (`.kebab/sources.json`) maps each source
ID to a `stem`. The processed text lives at
`processed/documents/<stem>/text.md`.

```python
def _load_source_texts(settings, fm_sources) -> list[tuple[str, int, str]]:
    """Return (title, source_id, text) for each source with processed text."""
    index = load_source_index(settings)
    texts = []
    for src in fm_sources:
        entry = index.get(src.id)
        text_path = settings.PROCESSED_DIR / "documents" / entry.stem / "text.md"
        if text_path.exists():
            texts.append((src.title, src.id, text_path.read_text()))
    return texts
```

### Integration with research_gaps.py

In `run()`, after loading gaps and before the external search loop:

```python
# Try local sources first
source_texts = _load_source_texts(settings, fm.sources)
if source_texts and gaps:
    local_answers = _answer_from_sources(settings, fm.name, gaps, source_texts)
    # Apply local answers, mark those gaps as answered
    # Remaining unanswered gaps proceed to external search
```

### Model

Uses `settings.RESEARCH_EXECUTOR_MODEL` (same as batch verifier).
The model name is included in the answer tag.

## Architecture

### New file

`app/agents/research_gaps/local_answerer.py`
- `LocalGapAnswer` model
- `LocalAnswererDeps` dataclass
- `answer_from_sources()` function (pydantic-ai agent)
- `_build_local_prompt()` helper

### Modified files

`app/agents/research_gaps/research_gaps.py`
- Add local-source-first step before external search
- `_load_source_texts()` helper
- Apply local answers and filter them out of the external search loop

`app/agents/research_gaps/writer.py`
- Support the new answer tag format (model name instead of URL)

### Prompt file

`app/agents/research_gaps/prompts/local_answerer.md`

## Testing

### Unit tests

- `test_local_answerer.py` — mock agent, verify LocalGapAnswer model,
  verify prompt includes source text and gap questions
- Test `_load_source_texts` with tmp_path fixtures

### Integration tests

- Full research-gaps run with local sources answering some gaps and
  external search answering others

## Source text size

Each source's processed text is truncated to 8000 tokens before
inclusion in the prompt. A typical article cites 2-3 sources at ~8K
tokens each = ~24K tokens of source content + gaps + prompt. Fits
comfortably in gemini-flash's context window.

If an article cites many sources (>5), only include the sources whose
titles are most relevant to the gap questions. For now, include all
sources — optimization deferred.

## Out of Scope

- Chunking/embedding source text for semantic search — we pass full
  text (truncated) to the LLM
- Cross-article source lookup (only the article's own cited sources)
- Verification of LLM-generated local answers — the source text IS
  the authority for these answers
