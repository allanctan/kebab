"""Merge multiple append findings per section into one cohesive statement.

When the verify loop produces N separate "append" findings for the same
section (e.g. three paraphrases of the same fact from the same source),
this step synthesizes them into one paragraph that reads naturally and
preserves all source citations.

Called by the orchestrator between the verify and write steps:
``plan → search → verify → **synthesize** → write``
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.agents.research.verifier import FindingResult, FindingTuple
from app.config.config import Settings
from app.core.llm.resolve import resolve_model

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "synthesizer.md"


class SynthesizedAppend(BaseModel):
    """Output of the synthesizer — one merged statement for a section."""

    model_config = ConfigDict(extra="forbid")

    sentence: str = Field(
        ...,
        description="One cohesive paragraph synthesizing all appended facts. "
        "Must reference each source by its [N] marker.",
    )


@dataclass
class SynthesizerDeps:
    settings: Settings
    section: str
    sentences: list[str]
    source_markers: list[str]


def _build_synthesizer_agent(settings: Settings) -> Agent[SynthesizerDeps, SynthesizedAppend]:
    return Agent(
        model=resolve_model(settings.RESEARCH_EXECUTOR_MODEL),
        deps_type=SynthesizerDeps,
        output_type=SynthesizedAppend,
        system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def _synthesize_one_section(
    settings: Settings,
    section: str,
    appends: list[tuple[str, str, str]],
    *,
    agent: Agent[SynthesizerDeps, SynthesizedAppend] | None = None,
) -> str:
    """Merge multiple (sentence, source_title, source_marker) tuples into one statement.

    Returns the synthesized sentence with inline source markers (e.g. ``[^3][^5]``).
    """
    agent = agent or _build_synthesizer_agent(settings)

    sentences = [s for s, _, _ in appends]
    markers = [m for _, _, m in appends]

    deps = SynthesizerDeps(
        settings=settings,
        section=section,
        sentences=sentences,
        source_markers=markers,
    )
    parts = [
        f"section: {section}",
        "",
        "Appended statements to merge:",
    ]
    for i, (sentence, title, marker) in enumerate(appends, 1):
        parts.append(f"  {i}. {sentence} (source: {title}, marker: {marker})")
    parts.append("")
    parts.append(f"Source markers to preserve: {', '.join(markers)}")

    user = "\n".join(parts)
    logger.debug("synthesizer input for section %r: %d statements", section, len(appends))
    result = agent.run_sync(user, deps=deps).output
    logger.info(
        "synthesizer: merged %d appends for section %r into one statement",
        len(appends),
        section,
    )
    return result.sentence


def merge_appends(
    settings: Settings,
    findings: list[FindingTuple],
    footnote_refs: dict[str, str],
) -> list[FindingTuple]:
    """Pre-process findings to merge multiple appends per section.

    ``footnote_refs`` maps ``source_url → footnote_marker`` (e.g. ``"[^3]"``),
    built by the caller before this step so the synthesizer knows which
    markers to preserve.

    For sections with a single append, the finding passes through unchanged.
    For sections with 2+ appends, the LLM synthesizes them into one cohesive
    statement and the group is replaced with a single merged finding.
    """
    # Separate appends from non-appends
    non_appends: list[FindingTuple] = []
    appends_by_section: dict[str, list[FindingTuple]] = defaultdict(list)

    for finding_tuple in findings:
        claim, finding, _title, _url = finding_tuple
        if finding.outcome == "append" and finding.new_sentence and claim.section != "Research Gaps":
            appends_by_section[claim.section].append(finding_tuple)
        else:
            non_appends.append(finding_tuple)

    merged: list[FindingTuple] = list(non_appends)

    for section, section_appends in appends_by_section.items():
        if len(section_appends) == 1:
            merged.append(section_appends[0])
            continue

        # Build the (sentence, source_title, source_marker) tuples for synthesis
        synth_input: list[tuple[str, str, str]] = []
        all_urls: list[str] = []
        for claim, finding, source_title, source_url in section_appends:
            marker = footnote_refs.get(source_url, "")
            synth_input.append((finding.new_sentence or "", source_title, marker))
            all_urls.append(source_url)

        synthesized = _synthesize_one_section(settings, section, synth_input)

        # Use the first finding as the anchor; replace its new_sentence
        anchor_claim, anchor_finding, anchor_title, anchor_url = section_appends[0]
        merged_finding = FindingResult(
            outcome="append",
            reasoning=f"Synthesized from {len(section_appends)} appended findings.",
            evidence_quote=anchor_finding.evidence_quote,
            new_sentence=synthesized,
        )
        merged.append((anchor_claim, merged_finding, anchor_title, anchor_url))

        # For the remaining source URLs, add confirm-style entries so the
        # writer still creates their footnote definitions. The synthesized
        # sentence already contains the markers inline.
        for _, _, other_title, other_url in section_appends[1:]:
            if other_url != anchor_url:
                # Add a no-op confirm that just ensures the footnote def exists
                noop_finding = FindingResult(
                    outcome="confirm",
                    reasoning="Footnote anchor for synthesized append.",
                    evidence_quote="(merged into synthesized statement)",
                )
                merged.append((anchor_claim, noop_finding, other_title, other_url))

    return merged


__all__ = ["merge_appends"]
