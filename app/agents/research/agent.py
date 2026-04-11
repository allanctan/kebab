"""Research agent orchestrator — wires planner and executor together.

Stage 3 of the research pipeline. Finds an article by ID, runs the
planner to extract claims and generate queries, executes queries via
adapters, classifies findings, judges disputes, applies findings to
the article body, and updates frontmatter with research metadata.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import httpx

from pydantic import BaseModel, ConfigDict, Field

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
    plan_research,
)
from app.config.config import Settings
from app.core.sources.fetcher import user_agent
from app.core.markdown import (
    count_external_footnotes,
    extract_disputes,
    extract_research_gaps,
    read_article,
    remove_research_gap,
    write_article,
)
from app.pipeline.ingest.inbox import stage_to_inbox

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public type aliases — lets callers swap stubs without importing internals
# ---------------------------------------------------------------------------

# (settings, deps) -> ResearchPlan
PlannerFn = Callable[..., ResearchPlan]

# (adapter_name, query, settings) -> [(title, url, content)]
SearcherFn = Callable[[str, str, Settings], list[tuple[str, str, str]]]

# (settings, claim, source_title, source_content) -> FindingResult
ClassifierFn = Callable[..., FindingResult]


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class ResearchResult(BaseModel):
    """Summary of one research run."""

    model_config = ConfigDict(extra="forbid")

    article_id: str = Field(..., description="ID of the researched article.")
    claims_total: int = Field(default=0, description="Number of claims extracted.")
    confirms: int = Field(default=0, description="Number of confirmed claims.")
    appends: int = Field(default=0, description="Number of appended facts.")
    disputes: int = Field(default=0, description="Number of genuine disputes found.")
    findings: list[str] = Field(
        default_factory=list,
        description="Human-readable summary of each finding.",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_article_path(settings: Settings, article_id: str) -> Path | None:
    """Scan CURATED_DIR recursively for the article with the given ID."""
    curated = settings.CURATED_DIR
    for path in curated.rglob("*.md"):
        try:
            fm, _ = read_article(path)
        except Exception:
            continue
        if fm.id == article_id:
            return path
    return None


def _default_searcher(
    adapter_name: str,
    query: str,
    settings: Settings,
) -> list[tuple[str, str, str]]:
    """Discover and fetch candidates via the named adapter.

    Returns a list of ``(title, url, content)`` tuples.  At most 2
    candidates are fetched per query to stay within the research budget.
    Fetch failures are logged and skipped rather than propagated.

    For Wikipedia candidates the locator is the article title; we
    construct the canonical URL as
    ``https://en.wikipedia.org/wiki/<locator>`` and also attempt a full
    fetch so the raw text lands in ``raw/inbox/`` for provenance.
    """
    from app.pipeline.ingest.registry import build_default_registry

    registry = build_default_registry(settings)
    try:
        adapter = registry.get(adapter_name)
    except Exception:
        logger.warning("research: unknown adapter %r — skipping query %r", adapter_name, query)
        return []

    candidates = adapter.discover(query, limit=3)
    results: list[tuple[str, str, str]] = []

    for candidate in candidates[:2]:
        title = candidate.title
        locator = candidate.locator

        # Build URL — Wikipedia uses title as locator; others supply a URL
        if adapter_name == "wikipedia":
            url = f"https://en.wikipedia.org/wiki/{quote(locator, safe='')}"
        else:
            url = locator if locator.startswith("http") else f"https://{locator}"

        try:
            artifact = adapter.fetch(candidate)
            content_bytes = artifact.raw_path.read_bytes()
            content = content_bytes.decode("utf-8", errors="replace")
            # Stage a copy in inbox for provenance
            filename = f"research_{artifact.raw_path.name}"
            stage_to_inbox(settings.KNOWLEDGE_DIR, filename, content_bytes)
        except Exception as exc:
            logger.warning(
                "research: fetch failed for %r (%s) — %s", title, url, exc
            )
            continue

        results.append((title, url, content))

    return results


def _download_research_image(
    image_url: str,
    description: str,
    article_path: Path,
    article_slug: str,
) -> str | None:
    """Download an image and return its relative markdown path, or None on failure."""
    try:
        response = httpx.get(
            image_url,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": user_agent()},
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("research: image download failed for %s: %s", image_url, exc)
        return None

    ext = Path(image_url.split("?")[0]).suffix or ".png"
    slug = re.sub(r"[^a-z0-9]+", "-", description.lower().strip())[:40].strip("-") or "image"
    filename = f"wiki-{slug}{ext}"

    dest_dir = article_path.parent / "figures" / article_slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    dest.write_bytes(response.content)
    return f"figures/{article_slug}/{filename}"


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def run(
    settings: Settings,
    *,
    article_id: str,
    planner: PlannerFn = plan_research,
    searcher: SearcherFn = _default_searcher,
    classifier: ClassifierFn = classify_finding,
    budget: int = 10,
) -> ResearchResult:
    """Research an article: plan → search → classify → apply → write.

    Args:
        settings:   KEBAB runtime configuration.
        article_id: ID of the article to research.
        planner:    Callable that returns a :class:`ResearchPlan`.  Can be
                    replaced with a stub in tests.
        searcher:   Callable ``(adapter, query, settings) -> [(title, url, content)]``.
                    Can be replaced with a stub in tests.
        classifier: Callable that returns a :class:`FindingResult`.  Can be
                    replaced with a stub in tests.  In the real flow, when the
                    classifier returns a ``dispute`` outcome the orchestrator
                    calls :func:`judge_dispute` to confirm it is genuine.
        budget:     Maximum number of queries to execute.

    Returns:
        A :class:`ResearchResult` summarising the run.
    """
    path = _find_article_path(settings, article_id)
    if path is None:
        logger.warning("research: article %r not found — skipping", article_id)
        return ResearchResult(article_id=article_id)

    fm, body = read_article(path)
    article_name = fm.name

    # ------------------------------------------------------------------
    # Step 1: Plan
    # ------------------------------------------------------------------
    from app.pipeline.ingest.registry import build_default_registry
    registry = build_default_registry(settings)
    # Only include adapters useful for claim verification.
    # Exclude: openstax (book-level only), local_pdf/local_dataset/direct_url (not search engines).
    # Exclude tavily if no API key is configured.
    verification_adapters = {"wikipedia", "tavily"}
    tavily_key = getattr(settings, "TAVILY_API_KEY", "")
    if not tavily_key:
        verification_adapters.discard("tavily")
    available = [n for n in registry.names() if n in verification_adapters]

    research_gaps = extract_research_gaps(body)

    deps = PlannerDeps(
        settings=settings,
        article_name=article_name,
        article_body=body,
        available_adapters=available,
        budget_hint=budget,
        research_gaps=research_gaps,
    )
    plan: ResearchPlan = planner(settings, deps)
    logger.info(
        "research: %d claims, %d queries for %r",
        len(plan.claims),
        len(plan.queries),
        article_id,
    )

    # ------------------------------------------------------------------
    # Step 2: Execute queries and collect (claim, finding, title, url)
    # ------------------------------------------------------------------
    findings: list[FindingTuple] = []
    confirms = 0
    appends = 0
    disputes = 0
    finding_summaries: list[str] = []

    queries_run = 0
    for sq in plan.queries:
        if queries_run >= budget:
            logger.info("research: budget of %d queries reached", budget)
            break

        sources = searcher(sq.adapter, sq.query, settings)
        queries_run += 1

        for source_title, source_url, source_content in sources:
            for claim_idx in sq.target_claims:
                if claim_idx >= len(plan.claims):
                    continue
                claim: ClaimEntry = plan.claims[claim_idx]

                result: FindingResult = classifier(
                    settings, claim, source_title, source_content
                )

                # In the real flow, disputes go through the judge.
                # When the classifier is a stub (i.e. not the real
                # classify_finding), we honour the finding directly so tests
                # can inject dispute outcomes without triggering LLM calls.
                if result.outcome == "dispute" and classifier is classify_finding:
                    judgment = judge_dispute(settings, claim, result, source_content)
                    if not judgment.is_genuine:
                        logger.debug(
                            "research: dispute dismissed for claim %r", claim.text
                        )
                        continue

                findings.append((claim, result, source_title, source_url))
                summary = (
                    f"{result.outcome}: {claim.text[:60]!r} "
                    f"via {source_title!r}"
                )
                finding_summaries.append(summary)
                logger.info("research: %s", summary)

                if result.outcome == "confirm":
                    confirms += 1
                elif result.outcome == "append":
                    appends += 1
                elif result.outcome == "dispute":
                    disputes += 1

    # ------------------------------------------------------------------
    # Step 3: Apply findings to the article body
    # ------------------------------------------------------------------
    if findings:
        new_body = apply_findings_to_article(body, findings)
    else:
        new_body = body

    # ------------------------------------------------------------------
    # Step 3b: Download Wikipedia images, describe via LLM, add as
    # footnote-style references.
    # ------------------------------------------------------------------
    from app.core.images.figures import FigureEntry
    from app.core.llm.multimodal import describe_image
    from app.pipeline.ingest.adapters.wikipedia import fetch_article_images

    # Prefilter keywords — loaded from file so the list can grow over time.
    _skip_file = Path(settings.KNOWLEDGE_DIR) / ".kebab" / "image_skip_keywords.txt"
    _skip_keywords: list[str] = []
    if _skip_file.exists():
        _skip_keywords = [
            line.strip().lower()
            for line in _skip_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    wiki_figures: list[FigureEntry] = []
    fig_num = 1  # local numbering for wiki figures
    seen_titles: set[str] = set()

    for claim, finding, source_title, source_url in findings:
        if finding.outcome not in ("confirm", "append"):
            continue
        if "wikipedia.org" not in source_url:
            continue
        wiki_title = source_url.split("/wiki/")[-1].replace("%20", " ")
        if wiki_title in seen_titles:
            continue
        seen_titles.add(wiki_title)
        try:
            images = fetch_article_images(wiki_title, limit=3)
        except Exception:
            continue

        for img in images[:2]:
            # Step 1: Prefilter by Wikipedia description
            desc_lower = img.get("description", "").lower()
            if any(skip in desc_lower for skip in _skip_keywords):
                logger.debug("research: skipping image %r — prefilter match", img["title"])
                continue

            # Step 2: Download
            rel_path = _download_research_image(
                img["url"], img["description"], path, path.stem,
            )
            if not rel_path:
                continue
            abs_path = path.parent / rel_path

            # Step 3: LLM describe (same as PDF figure describer)
            mime = "image/svg+xml" if abs_path.suffix == ".svg" else f"image/{abs_path.suffix.lstrip('.')}"
            try:
                image_bytes = abs_path.read_bytes()
                description = describe_image(
                    image_bytes, mime, settings,
                    context_hint=f"From Wikipedia article: {wiki_title}",
                )
            except Exception as exc:
                logger.debug("research: describe failed for %s: %s", rel_path, exc)
                description = img.get("description", "Wikipedia image")[:100]

            # Drop if LLM says decorative (icons that slipped past prefilter)
            if description == "DECORATIVE":
                logger.debug("research: dropping decorative wiki image %s", rel_path)
                abs_path.unlink(missing_ok=True)
                continue

            wiki_figures.append(FigureEntry(
                local_num=fig_num,
                figure_id=abs_path.stem,
                description=description,
                source_path=abs_path,
                mime_type=mime,
            ))
            fig_num += 1

    # Add wiki figures as [FIGURE:N] definitions at the end of the body
    if wiki_figures:
        fig_defs: list[str] = []
        for fig in wiki_figures:
            ext = fig.source_path.suffix
            rel = f"figures/{path.stem}/{fig.figure_id}{ext}"
            fig_defs.append(f"\n![{fig.description[:150]}]({rel})")
        new_body = new_body.rstrip() + "\n" + "\n".join(fig_defs) + "\n"

    # ------------------------------------------------------------------
    # Step 3c: Answer gaps in-place in Research Gaps section
    # ------------------------------------------------------------------
    if research_gaps and findings:
        for claim, finding, source_title, source_url in findings:
            if finding.outcome == "append" and claim.section == "Research Gaps" and finding.new_sentence:
                # Replace the gap question with Q&A format in the same section
                old_line = f"- {claim.text}"
                answered = (
                    f"- **Q: {claim.text}**\n"
                    f"  **A:** {finding.new_sentence}"
                )
                new_body = new_body.replace(old_line, answered, 1)

    # ------------------------------------------------------------------
    # Step 4: Update frontmatter extras and write back
    # ------------------------------------------------------------------
    extra: dict[str, object] = {
        "research_claims_total": len(plan.claims),
        "external_confirms": count_external_footnotes(new_body),
        "dispute_count": extract_disputes(new_body),
        "researched_at": date.today().isoformat(),
    }
    # FrontmatterSchema uses extra="allow" so we can write arbitrary keys
    for key, value in extra.items():
        setattr(fm, key, value)

    write_article(path, fm, new_body)
    logger.info(
        "research: wrote %r — confirms=%d appends=%d disputes=%d",
        path.name,
        confirms,
        appends,
        disputes,
    )

    return ResearchResult(
        article_id=article_id,
        claims_total=len(plan.claims),
        confirms=confirms,
        appends=appends,
        disputes=disputes,
        findings=finding_summaries,
    )
