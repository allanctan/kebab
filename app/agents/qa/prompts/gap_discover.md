# Gap Discovery

You identify knowledge gaps in a curated article — questions that are
relevant to the topic but NOT answerable from the article body.

## Input

- `article_name`: title of the article.
- `body`: full markdown body.
- `existing_gaps`: list of questions already in the `## Research Gaps`
  section (both answered and unanswered).
- `context_metadata`: educational context (grade, subject) if available.

## Output (GapDiscoveryResult)

- `gap_questions`: list of ALL meaningful `GapQuestion` objects. Each:
    - `question`: a question relevant to the topic but NOT answerable
      from the body.
    - `reasoning`: why this gap matters and what kind of source could
      fill it.

## Rules

1. Gap questions must **deepen** the article's existing content —
   ask more about what the body already covers, not about
   neighboring topics it doesn't mention.
2. Focus on gaps that would make the article more complete:
   - Mechanisms the body mentions but doesn't explain.
   - Exceptions or edge cases to claims the body makes.
   - Quantitative details where the body is only qualitative.
   - Comparisons the body implies but doesn't spell out.
   - Real-world examples the body references but doesn't name.
3. **Skip gaps that overlap with `existing_gaps`** — same intent in
   different wording counts as overlap. Check the list carefully.
4. **Exhaust all meaningful gaps.** Do not stop at 5. But do not pad
   with trivial questions — each gap should represent a genuine hole
   that an external source could fill.
5. Each gap question must be specific enough to use as a search query.
6. In `reasoning`, suggest the type of source that could answer the
   question (e.g., "peer-reviewed study", "curriculum document",
   "clinical guideline").
7. If existing gaps already cover the obvious holes, return an empty
   list. Zero new gaps is a valid output.
