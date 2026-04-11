"""Research executor — searches external sources and classifies findings.

Stage 2 of the research agent. Runs the search plan from the planner,
evaluates each finding against article claims, and modifies the article
body with confirmations, appended facts, and disputes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from app.agents.research.planner import ClaimEntry
from app.config.config import Settings
from app.core.llm.resolve import resolve_model
from app.core.markdown import extract_section, next_footnote_number

# Matches existing footnote defs: [^N]: [Title](URL) or [^N]: [id] [Title](URL)
_EXISTING_FOOTNOTE_RE = re.compile(r"^\[\^(\d+)\]:\s.*?\((https?://[^)]+)\)", re.MULTILINE)

logger = logging.getLogger(__name__)

_EXECUTOR_PROMPT_PATH = Path(__file__).parent / "prompts" / "executor.md"
_JUDGE_PROMPT_PATH = Path(__file__).parent / "prompts" / "dispute_judge.md"


class FindingResult(BaseModel):
    """Classification of one external source finding against a claim."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["confirm", "append", "dispute"] = Field(
        ..., description="How this finding relates to the claim."
    )
    reasoning: str = Field(..., description="Why this classification.")
    evidence_quote: str = Field(..., description="Specific passage from the source.")
    new_sentence: str | None = Field(
        default=None, description="New sentence to append (append outcome only)."
    )
    contradiction: str | None = Field(
        default=None, description="Description of contradiction (dispute outcome only)."
    )


class DisputeJudgment(BaseModel):
    """Whether a flagged dispute is genuine or superficial."""

    model_config = ConfigDict(extra="forbid")

    is_genuine: bool = Field(..., description="True if real contradiction.")
    reasoning: str = Field(..., description="Explanation.")
    summary: str = Field(default="", description="Concise dispute description if genuine.")


# Type alias for a finding tuple: (claim, finding, source_title, source_url)
FindingTuple = tuple[ClaimEntry, FindingResult, str, str]


@dataclass
class ExecutorDeps:
    settings: Settings
    claim_text: str
    claim_section: str
    source_title: str
    source_content: str


@dataclass
class JudgeDeps:
    settings: Settings
    claim: str
    source_content: str
    initial_reasoning: str


def _build_executor_agent(settings: Settings) -> Agent[ExecutorDeps, FindingResult]:
    return Agent(
        model=resolve_model(settings.RESEARCH_EXECUTOR_MODEL),
        deps_type=ExecutorDeps,
        output_type=FindingResult,
        system_prompt=_EXECUTOR_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def _build_judge_agent(settings: Settings) -> Agent[JudgeDeps, DisputeJudgment]:
    return Agent(
        model=resolve_model(settings.RESEARCH_JUDGE_MODEL),
        deps_type=JudgeDeps,
        output_type=DisputeJudgment,
        system_prompt=_JUDGE_PROMPT_PATH.read_text(encoding="utf-8"),
        retries=settings.LLM_MAX_RETRIES,
    )


def classify_finding(
    settings: Settings,
    claim: ClaimEntry,
    source_title: str,
    source_content: str,
    *,
    agent: Agent[ExecutorDeps, FindingResult] | None = None,
) -> FindingResult:
    """Classify a single source against a single claim."""
    agent = agent or _build_executor_agent(settings)
    deps = ExecutorDeps(
        settings=settings,
        claim_text=claim.text,
        claim_section=claim.section,
        source_title=source_title,
        source_content=source_content,
    )
    user = (
        f"claim: {claim.text}\n"
        f"claim_section: {claim.section}\n"
        f"source_title: {source_title}\n\n"
        f"source_content:\n{source_content[:8000]}"
    )
    logger.debug(
        "classify input — claim: %r | source: %r",
        claim.text[:80],
        source_title,
    )
    result = agent.run_sync(user, deps=deps).output
    logger.debug(
        "classify output — outcome=%s | reasoning: %s",
        result.outcome,
        result.reasoning[:120],
    )
    return result


def judge_dispute(
    settings: Settings,
    claim: ClaimEntry,
    finding: FindingResult,
    source_content: str,
    *,
    agent: Agent[JudgeDeps, DisputeJudgment] | None = None,
) -> DisputeJudgment:
    """Determine if a flagged dispute is genuine."""
    agent = agent or _build_judge_agent(settings)
    deps = JudgeDeps(
        settings=settings,
        claim=claim.text,
        source_content=source_content,
        initial_reasoning=finding.reasoning,
    )
    user = (
        f"claim: {claim.text}\n"
        f"initial_reasoning: {finding.reasoning}\n"
        f"evidence_quote: {finding.evidence_quote}\n\n"
        f"source_content:\n{source_content[:4000]}"
    )
    logger.debug(
        "judge input — claim: %r | reasoning: %r | evidence: %r",
        claim.text[:80],
        finding.reasoning[:80],
        finding.evidence_quote[:80],
    )
    judgment = agent.run_sync(user, deps=deps).output
    logger.info(
        "judge output — genuine=%s | reasoning: %s | summary: %s",
        judgment.is_genuine,
        judgment.reasoning[:120],
        judgment.summary[:120] if judgment.summary else "(none)",
    )
    return judgment


def apply_findings_to_article(
    body: str,
    findings: list[FindingTuple],
) -> str:
    """Apply confirmed/appended/disputed findings to the article body.

    - confirm: add footnote citation to the claim's sentence
    - append: add new sentence with footnote after the relevant paragraph
    - dispute: add entry to ## Disputes section
    """
    footnote_num = next_footnote_number(body)
    new_footnote_defs: list[str] = []
    # Pre-populate with URLs already in the body from prior runs.
    url_to_footnote: dict[str, int] = {}
    for match in _EXISTING_FOOTNOTE_RE.finditer(body):
        num = int(match.group(1))
        url = match.group(2)
        if url.startswith("http"):
            url_to_footnote[url] = num
    disputes: list[str] = []
    appends: dict[str, list[str]] = {}  # section -> sentences to append

    def _get_footnote(source_title: str, source_url: str) -> str:
        nonlocal footnote_num
        if source_url in url_to_footnote:
            return f"[^{url_to_footnote[source_url]}]"
        num = footnote_num
        url_to_footnote[source_url] = num
        new_footnote_defs.append(
            f"[^{num}]: [{source_title}]({source_url})"
        )
        footnote_num += 1
        return f"[^{num}]"

    for claim, finding, source_title, source_url in findings:
        # Skip Research Gaps entirely — handled by Step 3c in the orchestrator
        if claim.section == "Research Gaps":
            continue

        if finding.outcome == "confirm":
            ref = _get_footnote(source_title, source_url)
            escaped = re.escape(claim.text)
            pattern = re.compile(f"({escaped})")
            if pattern.search(body):
                body = pattern.sub(rf"\1{ref}", body, count=1)

        elif finding.outcome == "append" and finding.new_sentence:
            ref = _get_footnote(source_title, source_url)
            sentence = f"{finding.new_sentence}{ref}"
            appends.setdefault(claim.section, []).append(sentence)

        elif finding.outcome == "dispute" and finding.contradiction:
            disputes.append(
                f"- **Claim**: \"{claim.text}\"\n"
                f"  **Section**: {claim.section}, paragraph {claim.paragraph}\n"
                f"  **External source**: [{source_title}]({source_url})\n"
                f"  **Contradiction**: {finding.contradiction}"
            )

    # Apply appends at end of their sections
    for section, sentences in appends.items():
        pattern_str = r"(^#{1,6}\s+" + re.escape(section) + r"\s*\n.*?)(?=^#{1,6}\s+|\Z)"
        section_pattern = re.compile(pattern_str, re.DOTALL | re.MULTILINE)
        match = section_pattern.search(body)
        if match:
            insert_text = "\n" + " ".join(sentences) + "\n"
            body = body[:match.end(1)] + insert_text + body[match.end(1):]

    # Ensure body ends cleanly before appending
    body = body.rstrip() + "\n"

    # Add disputes — append to existing section or create new one.
    # Dedup: skip disputes whose claim text is already in the section.
    if disputes:
        existing_disputes = extract_section(body, "Disputes")
        fresh_disputes = [
            d for d in disputes
            if d.split("\n")[0] not in (existing_disputes or "")
        ]
        if fresh_disputes:
            if existing_disputes:
                # Append to existing section
                disputes_text = "\n\n".join(fresh_disputes)
                # Find end of disputes section
                pattern = re.compile(
                    r"(^##\s+Disputes\s*\n.*?)(?=^##\s+|\Z)",
                    re.DOTALL | re.MULTILINE,
                )
                match = pattern.search(body)
                if match:
                    body = body[:match.end(1)] + "\n\n" + disputes_text + "\n" + body[match.end(1):]
            else:
                body += "\n## Disputes\n\n" + "\n\n".join(fresh_disputes) + "\n"

    # Add new footnote definitions
    if new_footnote_defs:
        body += "\n" + "\n".join(new_footnote_defs) + "\n"

    return body
