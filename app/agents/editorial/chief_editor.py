from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai import Agent

from app.config.config import Settings
from app.core.llm.resolve import resolve_model

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class ClaimRewrite(BaseModel):
    """A disputed claim that should be corrected based on authoritative sources."""

    model_config = ConfigDict(extra="forbid")

    original_claim: str = Field(
        ..., description="Exact text of the disputed claim in the article body."
    )
    corrected_claim: str = Field(
        ..., description="Rewritten claim reflecting the authoritative source."
    )
    source_url: str = Field(
        ..., description="URL of the authoritative source that won."
    )
    reasoning: str = Field(
        ..., description="Why the external source is more authoritative."
    )


class UnresolvableDispute(BaseModel):
    """A dispute that cannot be resolved — kept for human review."""

    model_config = ConfigDict(extra="forbid")

    claim: str = Field(..., description="The disputed claim text.")
    reasoning: str = Field(
        ...,
        description="Why the dispute cannot be resolved (opinion, no authoritative source, etc.).",
    )


class Verdict(BaseModel):
    """Chief editor's decision after reviewing the article state."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["loop", "accept"] = Field(
        ..., description="Whether to run another enrichment cycle or accept the article."
    )
    rewrites: list[ClaimRewrite] = Field(
        default_factory=list,
        description="Disputed claims to rewrite (external source wins).",
    )
    unresolvable: list[UnresolvableDispute] = Field(
        default_factory=list,
        description="Disputes kept for human review.",
    )
    unconfirmed_claims: list[str] = Field(
        default_factory=list,
        description="Claims still without external confirmation.",
    )
    reasoning: str = Field(
        ..., description="Why the editor chose to loop or accept."
    )

    @field_validator("decision", mode="before")
    @classmethod
    def _normalize_decision(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower()
        return v


@dataclass
class ChiefEditorDeps:
    """Runtime context for the chief editor agent."""

    settings: Settings
    article_id: str
    article_body: str
    frontmatter: dict
    disputes_section: str
    gaps_section: str
    audit_entries: list[dict]
    authoritative_sources: list[str]
    cycle: int
    max_cycles: int


def review(
    settings: Settings,
    deps: ChiefEditorDeps,
    *,
    agent: Agent[ChiefEditorDeps, Verdict] | None = None,
) -> Verdict:
    """Run the chief editor agent on an article's current state."""
    if agent is None:
        agent = Agent(
            model=resolve_model(settings.EDITORIAL_MODEL),
            deps_type=ChiefEditorDeps,
            output_type=Verdict,
            system_prompt=(_PROMPTS_DIR / "chief_editor.md").read_text(),
        )

    user_prompt = _build_user_prompt(deps)
    result = agent.run_sync(user_prompt, deps=deps)
    return result.output


def _build_user_prompt(deps: ChiefEditorDeps) -> str:
    """Assemble the user prompt from article state."""
    body = deps.article_body
    if len(body) > 8000:
        # Preserve ## Disputes and ## Research Gaps sections in full
        disputes_start = body.find("## Disputes")
        gaps_start = body.find("## Research Gaps")
        preserved = ""
        if disputes_start >= 0:
            preserved += "\n\n" + body[disputes_start:]
        elif gaps_start >= 0:
            preserved += "\n\n" + body[gaps_start:]
        body = body[:8000] + "\n\n[... truncated ...]\n" + preserved

    parts = [
        f"# Article: {deps.article_id}",
        f"Cycle {deps.cycle} of {deps.max_cycles}.",
        "",
        "## Article Body",
        body,
        "",
        "## Disputes Section",
        deps.disputes_section or "(no disputes)",
        "",
        "## Research Gaps Section",
        deps.gaps_section or "(no gaps)",
        "",
        "## Frontmatter (research stats)",
    ]
    for key in (
        "research_claims_total",
        "external_confirms",
        "dispute_count",
        "gaps_answered",
    ):
        val = deps.frontmatter.get(key, "n/a")
        parts.append(f"- {key}: {val}")

    if deps.authoritative_sources:
        parts.append("")
        parts.append("## Authoritative Sources for this vertical")
        for src in deps.authoritative_sources:
            parts.append(f"- {src}")

    if deps.audit_entries:
        parts.append("")
        parts.append("## Audit log (this cycle)")
        for entry in deps.audit_entries[-20:]:
            parts.append(
                f"- [{entry.get('action', '?')}] "
                f"{entry.get('detail', entry.get('claim', ''))}"
            )

    return "\n".join(parts)
