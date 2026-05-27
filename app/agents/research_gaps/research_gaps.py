"""Research-gaps agent — route each gap to the right answering strategy.

Reads the ``## Research Gaps`` section of a curated article. For each
unanswered question:

1. Categorize the question (factual / conceptual / pedagogical /
   open_ended / local_cultural) — in a single batch LLM call across all
   questions in the article.
2. Route to the right answer strategy:
   - ``factual``        → existing search → classifier flow
   - ``conceptual``/``pedagogical`` → Opus AI-answerer → Gemini-Pro verifier
   - ``open_ended``      → write a discussion-prompt marker, no LLM call
   - ``local_cultural``  → try factual flow; fall back to AI flow
3. Apply the verifier gate (``verify_passes``). Suppressed answers are
   replaced with a short italic note explaining *why*.
4. Write the result via the AST-based writer.

Per-gap processing runs concurrently up to ``settings.GAP_PARALLELISM``
using a thread pool; ``agent.run_sync`` is thread-safe.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from app.agents.research_gaps.ai_answerer import AIAnswer, ai_answer_gap
from app.agents.research_gaps.ai_verifier import (
    VerifierVerdict,
    verify_ai_answer,
    verify_passes,
)
from app.agents.research_gaps.categorizer import (
    GapCategorization,
    GapCategory,
    batch_categorize_gaps,
    categorize_gap,
)
from app.agents.research_gaps.classifier import answer_question
from app.agents.research_gaps.query_planner import (
    GapQueryPlan,
    QueryPlannerDeps,
    plan_queries,
)
from app.agents.research_gaps.writer import GapAnswer, apply_answers_to_gaps
from app.config.config import Settings
from app.core.audit import log_event
from app.core.llm.labels import short_model_label
from app.core.llm.resolve import resolve_model
from app.core.markdown import (
    extract_research_gaps,
    find_article_by_id,
    read_article,
    write_article,
)
from app.core.research.searcher import search
from app.core.verticals import resolve_vertical

# `categorize_gap` is re-exported so existing tests/integrations that still
# monkeypatch the per-gap function continue to import it from this module.
_ = categorize_gap

logger = logging.getLogger(__name__)


class GapsResult(BaseModel):
    """Summary of one research-gaps run."""

    model_config = ConfigDict(extra="forbid")

    article_id: str = Field(..., description="ID of the article processed.")
    gaps_total: int = Field(default=0, description="Number of unanswered gaps found.")
    answered: int = Field(default=0, description="Number of gaps answered this run.")
    findings: list[str] = Field(
        default_factory=list,
        description="Human-readable summary of each answered gap.",
    )


def _available_adapters(settings: Settings) -> list[str]:
    """Return verification-capable adapters available given current credentials."""
    adapters = ["wikipedia"]
    if getattr(settings, "TAVILY_API_KEY", ""):
        adapters.append("tavily")
    return adapters


# Domains blocked from answering gaps — homework sites, content farms,
# low-reliability Q&A aggregators, and social media.
# Gap answers introduce new claims into the article; they must come from
# trustworthy sources. These sites frequently contain user-generated or
# AI-generated content with no editorial oversight.
_BLOCKED_DOMAINS: frozenset[str] = frozenset(
    {
        "brainly.com",
        "brainly.ph",
        "brainly.in",
        "quora.com",
        "answers.com",
        "chegg.com",
        "coursehero.com",
        "studocu.com",
        "fiveable.me",
        "vaia.com",
        "studysmarter.com",
        "studysmarter.us",
        "reddit.com",
        "medium.com",
        "youtube.com",
        "facebook.com",
        "twitter.com",
        "x.com",
        "pinterest.com",
        "tiktok.com",
    }
)


def _is_blocked_domain(url: str) -> bool:
    """Return True if the URL's domain is in the blocklist."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    if host.startswith("www."):
        host = host[4:]
    return host in _BLOCKED_DOMAINS


def _provider_family(model_string: str) -> str:
    """Return a coarse family tag for the given model identifier."""
    s = model_string.lower()
    if "anthropic" in s or "claude" in s:
        return "anthropic"
    if "google" in s or "gemini" in s:
        return "google"
    if "openai" in s or "gpt" in s:
        return "openai"
    return "other"


def _warn_if_same_family(settings: Settings) -> None:
    """Log a WARNING if AI_ANSWERER_MODEL and AI_VERIFIER_MODEL look same-family."""
    a = _provider_family(settings.AI_ANSWERER_MODEL)
    v = _provider_family(settings.AI_VERIFIER_MODEL)
    if a == v and a != "other":
        logger.warning(
            "research-gaps: AI_ANSWERER_MODEL (%s) and AI_VERIFIER_MODEL (%s) "
            "are both in the %s family — cross-family verification is "
            "defeated. Consider switching one to a different provider.",
            settings.AI_ANSWERER_MODEL,
            settings.AI_VERIFIER_MODEL,
            a,
        )


def _short_label(settings: Settings, attr: str) -> str:
    """Resolve a Settings model field and return its short label.

    Falls back to the raw setting value if resolution fails (so a missing
    credential doesn't break disclaimer rendering in tests).
    """
    raw = getattr(settings, attr)
    try:
        resolved = resolve_model(raw)
        if isinstance(resolved, str):
            return short_model_label(resolved)
        return short_model_label(raw)
    except Exception:
        return short_model_label(raw)


def _factual_path(
    settings: Settings,
    *,
    gap_idx: int,
    gap_text: str,
    plan: GapQueryPlan,
    authoritative: list[str],
    article_path: Path,
    article_id: str,
) -> GapAnswer | None:
    """Existing search → classifier flow. Returns a GapAnswer or None."""
    for gq in plan.queries:
        if gq.target_gap_idx != gap_idx:
            continue
        sources = search(
            settings,
            gq.adapter,
            gq.query,
            limit=2,
            authoritative_sources=authoritative,
        )
        for src in sources:
            if _is_blocked_domain(src.url):
                logger.info("research-gaps: skipping blocked domain source %s", src.url)
                continue
            classification = answer_question(
                settings,
                question=gap_text,
                source_title=src.title,
                source_content=src.content,
            )
            if not classification.is_answered:
                continue
            log_event(
                article_path,
                stage="research-gaps",
                action="gap_answered",
                article_id=article_id,
                question=gap_text,
                answer=classification.answer,
                source_title=src.title,
                source_url=src.url,
            )
            return GapAnswer(
                gap_idx=gap_idx,
                answer_text=classification.answer,
                source_kind="external",
                source_title=src.title,
                source_url=src.url,
            )
    return None


def _ai_path(
    settings: Settings,
    *,
    gap_idx: int,
    gap_text: str,
    article_body: str,
    article_summary: str,
    category: GapCategory,
    article_path: Path,
    article_id: str,
    answerer_label: str,
    verifier_label: str,
) -> GapAnswer:
    """Answerer → verifier flow. Always returns SOME GapAnswer."""
    ai: AIAnswer = ai_answer_gap(
        settings,
        question=gap_text,
        article_body=article_body,
        article_summary=article_summary,
        category=category,
    )
    if not ai.is_answered:
        log_event(
            article_path,
            stage="research-gaps",
            action="gap_open_ended",
            article_id=article_id,
            question=gap_text,
            category=category,
            reasoning=ai.reasoning,
        )
        return GapAnswer(gap_idx=gap_idx, answer_text="", source_kind="discussion")

    verdict: VerifierVerdict = verify_ai_answer(
        settings,
        question=gap_text,
        answer=ai.answer,
        article_summary=article_summary,
    )
    if verify_passes(verdict):
        kind = "ai_article" if ai.grounded_in == "article_body" else "ai_general"
        log_event(
            article_path,
            stage="research-gaps",
            action="gap_ai_answered",
            article_id=article_id,
            question=gap_text,
            answer=ai.answer,
            category=category,
            verifier_verdict=verdict.verdict,
            verifier_confidence=verdict.confidence,
            model_answerer=answerer_label,
            model_verifier=verifier_label,
        )
        return GapAnswer(
            gap_idx=gap_idx,
            answer_text=ai.answer,
            source_kind=kind,
            answerer_model=answerer_label,
            verifier_model=verifier_label,
        )

    # Suppress — log full details + render a short rejection note.
    log_event(
        article_path,
        stage="research-gaps",
        action="gap_rejected",
        article_id=article_id,
        question=gap_text,
        rejected_answer=ai.answer,
        category=category,
        verifier_verdict=verdict.verdict,
        verifier_confidence=verdict.confidence,
        verifier_issue=verdict.issue,
        model_answerer=answerer_label,
        model_verifier=verifier_label,
    )
    if verdict.verdict == "agree" and verdict.confidence == "low":
        return GapAnswer(
            gap_idx=gap_idx,
            answer_text="",
            source_kind="verifier_low_confidence",
        )
    return GapAnswer(
        gap_idx=gap_idx,
        answer_text="",
        source_kind="verifier_rejected",
        rejection_reason=verdict.issue or "verifier flagged the answer",
    )


class _BudgetCounter:
    """Thread-safe counter shared by parallel gap processors.

    A budget of ``None`` means uncapped. Each worker calls
    :meth:`try_charge` to atomically test-and-increment the counter; if it
    returns ``False``, the worker exits early with a no-source answer.
    """

    def __init__(self, budget: int | None) -> None:
        self._budget = budget
        self._used = 0
        self._lock = threading.Lock()

    def try_charge(self, cost: int) -> bool:
        """Atomically reserve ``cost`` units. Returns ``True`` on success."""
        with self._lock:
            if self._budget is None:
                self._used += cost
                return True
            if self._used + cost > self._budget:
                return False
            self._used += cost
            return True

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def exhausted(self) -> bool:
        with self._lock:
            if self._budget is None:
                return False
            return self._used >= self._budget


def run(
    settings: Settings,
    *,
    article_id: str,
    budget: int | None = 5,
    no_ai: bool = False,
) -> GapsResult:
    """Answer unanswered gaps in an article.

    Args:
        settings:   KEBAB runtime configuration.
        article_id: ID of the article whose gaps should be answered.
        budget:     Maximum number of LLM calls across categorizer + answerer
                    + verifier + classifier. ``None`` = no cap.
        no_ai:      If True, fall back to the strict factual-only flow;
                    skip categorizer / answerer / verifier entirely.
    """
    _warn_if_same_family(settings)

    path = find_article_by_id(settings.CURATED_DIR, article_id)
    if path is None:
        logger.warning("research-gaps: article %r not found — skipping", article_id)
        return GapsResult(article_id=article_id)

    fm, body, tree = read_article(path)
    all_gaps = extract_research_gaps(tree)
    gaps = [g for g in all_gaps if not g.startswith("**Q:")]
    if not gaps:
        logger.info("research-gaps: no unanswered gaps for %r — skipping", article_id)
        return GapsResult(article_id=article_id)

    vertical = resolve_vertical(settings, fm)
    authoritative = vertical.authoritative_sources if vertical else []
    article_summary = str(fm.model_dump().get("summary", "") or "")

    answerer_label = _short_label(settings, "AI_ANSWERER_MODEL")
    verifier_label = _short_label(settings, "AI_VERIFIER_MODEL")

    # ---- Categorize (one batch call per article) -------------------------
    categories: list[GapCategory]
    if no_ai:
        # Strict factual-only flow: skip categorizer entirely.
        categories = ["factual"] * len(gaps)
    else:
        cats: list[GapCategorization] = batch_categorize_gaps(
            settings, gaps, article_summary
        )
        categories = [c.category for c in cats]
        for gap_text, cat in zip(gaps, cats):
            log_event(
                path,
                stage="research-gaps",
                action="gap_categorized",
                article_id=article_id,
                question=gap_text,
                category=cat.category,
                reasoning=cat.reasoning,
            )

    # ---- Plan factual queries (one call) ---------------------------------
    budget_hint = len(gaps) if budget is None else budget
    deps = QueryPlannerDeps(
        settings=settings,
        article_name=fm.name,
        gap_questions=gaps,
        available_adapters=_available_adapters(settings),
        budget_hint=budget_hint,
    )
    plan: GapQueryPlan = plan_queries(settings, deps)

    # The batch categorizer call (1) plus the planner call (1) are not
    # explicitly charged against the per-gap budget — the legacy code
    # didn't charge them either (categorizer was previously +1 per gap;
    # planner was always free). We preserve that accounting so existing
    # budget=20 / budget=50 tests still pass.

    # ---- Process gaps in parallel ----------------------------------------
    budget_counter = _BudgetCounter(budget)

    def _process_gap(gap_idx: int, gap_text: str, category: GapCategory) -> GapAnswer:
        """Per-gap closure — pure function over outer state, safe to thread."""
        if category == "factual":
            if not budget_counter.try_charge(1):  # classifier
                return GapAnswer(
                    gap_idx=gap_idx, answer_text="", source_kind="no_source"
                )
            answer = _factual_path(
                settings,
                gap_idx=gap_idx,
                gap_text=gap_text,
                plan=plan,
                authoritative=authoritative,
                article_path=path,
                article_id=article_id,
            )
            if answer is None:
                return GapAnswer(
                    gap_idx=gap_idx, answer_text="", source_kind="no_source"
                )
            return answer

        if category in ("conceptual", "pedagogical"):
            if no_ai:
                return GapAnswer(
                    gap_idx=gap_idx, answer_text="", source_kind="no_source"
                )
            if not budget_counter.try_charge(2):  # answerer + verifier
                return GapAnswer(
                    gap_idx=gap_idx, answer_text="", source_kind="no_source"
                )
            return _ai_path(
                settings,
                gap_idx=gap_idx,
                gap_text=gap_text,
                article_body=body,
                article_summary=article_summary,
                category=category,
                article_path=path,
                article_id=article_id,
                answerer_label=answerer_label,
                verifier_label=verifier_label,
            )

        if category == "open_ended":
            log_event(
                path,
                stage="research-gaps",
                action="gap_open_ended",
                article_id=article_id,
                question=gap_text,
                category=category,
            )
            return GapAnswer(gap_idx=gap_idx, answer_text="", source_kind="discussion")

        if category == "local_cultural":
            if not budget_counter.try_charge(1):  # factual classifier
                return GapAnswer(
                    gap_idx=gap_idx, answer_text="", source_kind="no_source"
                )
            answer = _factual_path(
                settings,
                gap_idx=gap_idx,
                gap_text=gap_text,
                plan=plan,
                authoritative=authoritative,
                article_path=path,
                article_id=article_id,
            )
            if answer is not None:
                return answer
            if no_ai:
                return GapAnswer(
                    gap_idx=gap_idx, answer_text="", source_kind="no_source"
                )
            if not budget_counter.try_charge(2):  # answerer + verifier
                return GapAnswer(
                    gap_idx=gap_idx, answer_text="", source_kind="no_source"
                )
            return _ai_path(
                settings,
                gap_idx=gap_idx,
                gap_text=gap_text,
                article_body=body,
                article_summary=article_summary,
                category=category,
                article_path=path,
                article_id=article_id,
                answerer_label=answerer_label,
                verifier_label=verifier_label,
            )

        # Literal exhaustiveness — unreachable.
        return GapAnswer(gap_idx=gap_idx, answer_text="", source_kind="no_source")

    answers: list[GapAnswer] = []
    max_workers = max(1, int(settings.GAP_PARALLELISM))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_process_gap, gap_idx, gap_text, category)
            for gap_idx, (gap_text, category) in enumerate(zip(gaps, categories))
        ]
        for future in futures:
            answers.append(future.result())

    # Sort by gap_idx so the writer sees stable ordering regardless of
    # which thread finished first.
    answers.sort(key=lambda a: a.gap_idx)

    finding_summaries = [
        f"answered gap {a.gap_idx} ({a.source_kind}): {gaps[a.gap_idx][:60]!r}"
        for a in answers
        if a.source_kind in ("external", "ai_article", "ai_general")
    ]

    new_body = apply_answers_to_gaps(body, gaps, answers) if answers else body

    answered_count = sum(
        1 for a in answers if a.source_kind in ("external", "ai_article", "ai_general")
    )
    previous_answered = int(fm.model_dump().get("gaps_answered", 0) or 0)
    setattr(fm, "gaps_answered", previous_answered + answered_count)
    setattr(fm, "gaps_researched_at", date.today().isoformat())

    write_article(path, fm, new_body)
    logger.info(
        "research-gaps: wrote %r — answered=%d/%d (LLM calls=%d)",
        path.name,
        answered_count,
        len(gaps),
        budget_counter.used,
    )

    return GapsResult(
        article_id=article_id,
        gaps_total=len(gaps),
        answered=answered_count,
        findings=finding_summaries,
    )


__all__ = ["GapsResult", "run"]
