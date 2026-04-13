"""Editorial orchestrator — Phase 2 enrich loop for a single article.

Cycles through qa → research-gaps → research → chief editor judgment until
the chief editor accepts or max_cycles is reached.

No LLM calls live here. All intelligence is delegated to the sub-agents and
the chief editor. This module is pure Python orchestration — mockable and
testable without any API credentials.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.agents.editorial.chief_editor import ChiefEditorDeps, Verdict
from app.agents.editorial.chief_editor import review as chief_editor_review
from app.agents.editorial.writer import annotate_unresolvable, apply_rewrites
from app.config.config import Settings
from app.core.audit import log_event, read_log
from app.core.markdown import (
    extract_section,
    find_article_by_id,
    read_article,
    write_article,
)
from app.core.verticals import resolve_vertical

# Supervisor exception: editorial orchestrates qa in the enrich loop.
# Long-term fix: promote agent entry points to a core registry.
from app.agents.qa import qa as qa_module
# Supervisor exception: editorial orchestrates research in the enrich loop.
# Long-term fix: promote agent entry points to a core registry.
from app.agents.research import research as research_module
# Supervisor exception: editorial orchestrates research-gaps in the enrich loop.
# Long-term fix: promote agent entry points to a core registry.
from app.agents.research_gaps import research_gaps as gaps_module
# Supervisor exception: editorial syncs to Qdrant after each cycle.
# Long-term fix: promote agent entry points to a core registry.
from app.agents.sync import auto_sync

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class EditorialResult:
    """Summary of one editorial run."""

    article_id: str
    cycles: int = 0
    decision: Literal["accept", "max_cycles_reached"] = "accept"
    claims_confirmed: int = 0
    claims_total: int = 0
    disputes_resolved: int = 0
    disputes_unresolvable: int = 0
    gaps_answered: int = 0


# ---------------------------------------------------------------------------
# Sub-agent wrappers (module-level for mockability)
# ---------------------------------------------------------------------------


def _run_qa(settings: Settings, article_id: str) -> qa_module.QaRunResult:
    """Run the gap discovery agent for a single article."""
    return qa_module.run(settings, article_id=article_id, once=True)


def _run_research_gaps(settings: Settings, article_id: str) -> gaps_module.GapsResult:
    """Run the research-gaps agent for a single article."""
    return gaps_module.run(settings, article_id=article_id)


def _run_research(settings: Settings, article_id: str) -> research_module.ResearchResult:
    """Run the claim verification agent for a single article."""
    return research_module.run(settings, article_id=article_id)


def _run_chief_editor(
    settings: Settings,
    *,
    article_id: str,
    body: str,
    fm_dict: dict,
    disputes_section: str,
    gaps_section: str,
    audit_entries: list[dict],
    cycle: int,
    max_cycles: int,
) -> Verdict:
    """Build deps and call the chief editor agent."""

    class _FmProxy:
        def __init__(self, data: dict) -> None:
            self._data = data

        def model_dump(self) -> dict:
            return self._data

    vertical = resolve_vertical(settings, _FmProxy(fm_dict))  # type: ignore[arg-type]
    authoritative_sources = vertical.authoritative_sources if vertical else []

    deps = ChiefEditorDeps(
        settings=settings,
        article_id=article_id,
        article_body=body,
        frontmatter=fm_dict,
        disputes_section=disputes_section,
        gaps_section=gaps_section,
        audit_entries=audit_entries,
        authoritative_sources=authoritative_sources,
        cycle=cycle,
        max_cycles=max_cycles,
    )
    return chief_editor_review(settings, deps)


# ---------------------------------------------------------------------------
# Article state helpers (module-level for mockability)
# ---------------------------------------------------------------------------


def _load_article_state(
    path: object,  # Path — typed as object so tests can pass a MagicMock
) -> tuple[str, dict, str, str, list[dict]]:
    """Read article from disk and extract sections needed by the chief editor.

    Returns:
        (body, fm_dict, disputes_section, gaps_section, audit_entries)
    """
    from pathlib import Path as _Path

    fm, body, tree = read_article(_Path(str(path)))  # type: ignore[arg-type]
    fm_dict = fm.model_dump()
    disputes_section = extract_section(tree, "Disputes")
    gaps_section = extract_section(tree, "Research Gaps")
    audit_entries = read_log(_Path(str(path)))  # type: ignore[arg-type]
    return body, fm_dict, disputes_section, gaps_section, audit_entries


def _write_final_frontmatter(
    path: object,
    result: EditorialResult,
) -> None:
    """Stamp editorial metadata onto the article frontmatter."""
    from pathlib import Path as _Path

    fm, body, _ = read_article(_Path(str(path)))  # type: ignore[arg-type]
    setattr(fm, "editorial_cycles", result.cycles)
    setattr(fm, "editorial_decision", result.decision)
    setattr(fm, "unresolvable_dispute_count", result.disputes_unresolvable)
    setattr(fm, "editorial_at", datetime.now().isoformat(timespec="seconds"))
    write_article(_Path(str(path)), fm, body)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Mid-cycle resumption
# ---------------------------------------------------------------------------


def _completed_stages_in_current_cycle(article_path: object) -> set[str]:
    """Check the audit log for stages completed after the last cycle_start.

    If the last editorial event is a ``cycle_start`` followed by sub-agent
    events, those sub-agents already ran successfully. Returns the set of
    stage names (e.g. ``{"qa", "research-gaps"}``) that can be skipped.
    """
    from pathlib import Path as _Path

    entries = read_log(_Path(str(article_path)))
    if not entries:
        return set()

    # Walk backwards to find the last editorial cycle_start
    last_cycle_idx = -1
    for i in range(len(entries) - 1, -1, -1):
        if entries[i].get("stage") == "editorial" and entries[i].get("action") == "cycle_start":
            last_cycle_idx = i
            break

    if last_cycle_idx < 0:
        return set()

    # Check if this cycle already completed (has a verdict)
    events_after = entries[last_cycle_idx + 1 :]
    for e in events_after:
        if e.get("stage") == "editorial" and e.get("action") == "verdict":
            return set()  # Cycle finished — no resumption needed

    # Collect stages that logged events after cycle_start
    return {e["stage"] for e in events_after if "stage" in e}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(
    settings: Settings,
    *,
    article_id: str,
    max_cycles: int = 3,
) -> EditorialResult:
    """Run the editorial enrich loop for one article.

    Cycles: qa → research-gaps → research → chief editor, until the
    chief editor accepts or ``max_cycles`` is exhausted.

    Args:
        settings:   KEBAB runtime configuration.
        article_id: ID of the article to enrich.
        max_cycles: Maximum number of enrichment cycles before giving up.

    Returns:
        :class:`EditorialResult` summarising the run.
    """
    article_path = find_article_by_id(settings.CURATED_DIR, article_id)
    if article_path is None:
        logger.warning("editorial: article %r not found — skipping", article_id)
        return EditorialResult(article_id=article_id, decision="max_cycles_reached")

    result = EditorialResult(article_id=article_id)
    decision: Literal["accept", "max_cycles_reached"] = "max_cycles_reached"

    # Check for a partially-completed cycle from a previous run
    already_done = _completed_stages_in_current_cycle(article_path)
    resuming = bool(already_done)
    if resuming:
        logger.info(
            "editorial: [%s] resuming — stages already done: %s",
            article_id,
            ", ".join(sorted(already_done)),
        )

    for cycle in range(1, max_cycles + 1):
        if not resuming:
            logger.info(
                "editorial: [%s] cycle %d/%d — starting qa → research-gaps → research",
                article_id,
                cycle,
                max_cycles,
            )
            log_event(
                article_path,
                stage="editorial",
                action="cycle_start",
                article_id=article_id,
                detail=f"cycle {cycle}/{max_cycles}",
            )

        # --- sub-agents (skip if already completed in a resumed cycle) ---
        if "qa" not in already_done:
            qa_result = _run_qa(settings, article_id)
            result.gaps_answered += qa_result.gaps_added
        else:
            logger.info("editorial: [%s] skipping qa (already ran)", article_id)

        if "research-gaps" not in already_done:
            gaps_result = _run_research_gaps(settings, article_id)
            result.gaps_answered += gaps_result.answered
        else:
            logger.info("editorial: [%s] skipping research-gaps (already ran)", article_id)

        if "research" not in already_done:
            research_result = _run_research(settings, article_id)
            result.claims_total = research_result.claims_total
            result.claims_confirmed += research_result.confirms
        else:
            logger.info("editorial: [%s] skipping research (already ran)", article_id)

        # Clear resumption state — subsequent cycles run from scratch
        already_done = set()

        # --- re-read article after sub-agents may have modified it ---
        body, fm_dict, disputes_section, gaps_section, audit_entries = _load_article_state(
            article_path
        )

        # --- chief editor ---
        verdict = _run_chief_editor(
            settings,
            article_id=article_id,
            body=body,
            fm_dict=fm_dict,
            disputes_section=disputes_section,
            gaps_section=gaps_section,
            audit_entries=audit_entries,
            cycle=cycle,
            max_cycles=max_cycles,
        )

        # --- apply verdict rewrites ---
        changed = False
        if verdict.rewrites:
            new_body = apply_rewrites(body, verdict.rewrites)
            result.disputes_resolved += len(verdict.rewrites)
            body = new_body
            changed = True
            for rw in verdict.rewrites:
                log_event(
                    article_path,
                    stage="editorial",
                    action="claim_rewrite",
                    article_id=article_id,
                    original=rw.original_claim[:100],
                    corrected=rw.corrected_claim[:100],
                    source_url=rw.source_url,
                )

        if verdict.unresolvable:
            body = annotate_unresolvable(body, verdict.unresolvable)
            result.disputes_unresolvable += len(verdict.unresolvable)
            changed = True
            for ud in verdict.unresolvable:
                log_event(
                    article_path,
                    stage="editorial",
                    action="dispute_unresolvable",
                    article_id=article_id,
                    claim=ud.claim[:100],
                    reasoning=ud.reasoning[:200],
                )

        if changed:
            fm, _, _ = read_article(article_path)
            write_article(article_path, fm, body)

        logger.info(
            "editorial: [%s] cycle %d verdict=%s rewrites=%d unresolvable=%d",
            article_id,
            cycle,
            verdict.decision,
            len(verdict.rewrites),
            len(verdict.unresolvable),
        )
        log_event(
            article_path,
            stage="editorial",
            action="verdict",
            article_id=article_id,
            detail=f"cycle {cycle}: {verdict.decision} — {verdict.reasoning[:120]}",
        )

        auto_sync(settings, "editorial")

        result.cycles = cycle

        if verdict.decision == "accept":
            decision = "accept"
            break

    result.decision = decision

    path = find_article_by_id(settings.CURATED_DIR, article_id)
    if path is not None:
        log_event(
            path,
            stage="editorial",
            action="complete",
            article_id=article_id,
            total_cycles=str(result.cycles),
            final_decision=result.decision,
        )

    _write_final_frontmatter(article_path, result)
    return result
