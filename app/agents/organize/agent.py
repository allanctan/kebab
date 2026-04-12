"""Pydantic-ai Agent that proposes a knowledge hierarchy from raw documents.

LLM helper used by the organize stage orchestrator.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, RunContext

from app.config.config import Settings
from app.core.llm.resolve import resolve_model

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_SYSTEM_PROMPT = (_PROMPTS_DIR / "organize.md").read_text(encoding="utf-8")
_INCREMENTAL_SYSTEM_PROMPT = (_PROMPTS_DIR / "incremental.md").read_text(encoding="utf-8")


HierarchyLevel = Literal["domain", "subdomain", "topic", "article"]


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return cleaned or "node"


class HierarchyNode(BaseModel):
    """One row in a proposed hierarchy plan."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Universal article ID, e.g. 'SCI-BIO-001'.")
    name: str = Field(..., description="Human-readable name.")
    level_type: HierarchyLevel = Field(..., description="Hierarchy depth tag.")
    parent_id: str | None = Field(
        default=None,
        description="ID of the parent node, or None for the root domain.",
    )
    description: str = Field(
        ..., description="One-sentence summary of what this node covers."
    )
    source_files: list[int] = Field(
        default_factory=list,
        description="Source index IDs that informed this node.",
    )
    md_path: str | None = Field(
        default=None,
        description="Canonical markdown path (set by the organize stage, not the LLM).",
    )


class HierarchyPlan(BaseModel):
    """Output of the organize agent — a flat list of nodes forming a tree."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[HierarchyNode] = Field(
        ..., description="Flat list of hierarchy nodes (parent_id forms the tree)."
    )


@dataclass
class OrganizeDeps:
    """Runtime context for the organize agent."""

    settings: Settings
    domain_hint: str
    manifest: list[tuple[str, str]]
    """``[(filename, first_2000_chars), …]`` for every raw doc considered."""


def build_organize_agent(settings: Settings) -> Agent[OrganizeDeps, HierarchyPlan]:
    """Construct the organize agent. Sync-callable via ``agent.run_sync``."""
    return Agent(
        model=resolve_model(settings.ORGANIZE_MODEL),
        deps_type=OrganizeDeps,
        output_type=HierarchyPlan,
        system_prompt=_SYSTEM_PROMPT,
        retries=settings.LLM_MAX_RETRIES,
    )


def propose_hierarchy(
    settings: Settings,
    domain_hint: str,
    manifest: list[tuple[str, str]],
    *,
    agent: Agent[OrganizeDeps, HierarchyPlan] | None = None,
) -> HierarchyPlan:
    """Synchronously call the agent and return the structured plan."""
    agent = agent or build_organize_agent(settings)

    @agent.system_prompt
    def _context(ctx: RunContext[OrganizeDeps]) -> str:
        manifest_str = "\n\n".join(
            f"### {filename}\n{snippet}" for filename, snippet in ctx.deps.manifest
        )
        return f"Domain hint: {ctx.deps.domain_hint}\n\nManifest:\n{manifest_str}"

    deps = OrganizeDeps(settings=settings, domain_hint=domain_hint, manifest=manifest)
    result = agent.run_sync("Propose a hierarchy.", deps=deps)
    return result.output


@dataclass
class IncrementalOrganizeDeps:
    """Runtime context for the incremental organize agent."""

    settings: Settings
    existing_plan: HierarchyPlan
    new_manifest: list[tuple[str, str]]


def build_incremental_organize_agent(
    settings: Settings,
) -> Agent[IncrementalOrganizeDeps, HierarchyPlan]:
    """Construct the incremental organize agent."""
    return Agent(
        model=resolve_model(settings.ORGANIZE_MODEL),
        deps_type=IncrementalOrganizeDeps,
        output_type=HierarchyPlan,
        system_prompt=_INCREMENTAL_SYSTEM_PROMPT,
        retries=settings.LLM_MAX_RETRIES,
    )


def _summarize_existing(plan: HierarchyPlan) -> str:
    """Render a compact text summary of an existing plan for the LLM."""
    lines: list[str] = []
    for node in plan.nodes:
        parent = f" parent={node.parent_id}" if node.parent_id else ""
        lines.append(
            f"- [{node.level_type}] {node.id}{parent} — {node.name}: {node.description}"
        )
    return "\n".join(lines) or "(empty)"


def propose_incremental_hierarchy(
    settings: Settings,
    existing_plan: HierarchyPlan,
    new_manifest: list[tuple[str, str]],
    *,
    agent: Agent[IncrementalOrganizeDeps, HierarchyPlan] | None = None,
) -> HierarchyPlan:
    """Ask the LLM to produce a delta plan extending ``existing_plan``.

    The returned :class:`HierarchyPlan` contains only the nodes to add or
    the existing nodes to extend (by id). Callers are responsible for
    merging the delta into the existing plan.
    """
    agent = agent or build_incremental_organize_agent(settings)

    @agent.system_prompt
    def _context(ctx: RunContext[IncrementalOrganizeDeps]) -> str:
        tree = _summarize_existing(ctx.deps.existing_plan)
        manifest_str = "\n\n".join(
            f"### {filename}\n{snippet}" for filename, snippet in ctx.deps.new_manifest
        )
        return f"existing_tree:\n{tree}\n\nnew_manifest:\n{manifest_str}"

    deps = IncrementalOrganizeDeps(
        settings=settings,
        existing_plan=existing_plan,
        new_manifest=new_manifest,
    )
    result = agent.run_sync(
        "Extend the plan with these new sources.", deps=deps
    )
    return result.output


__all__ = [
    "HierarchyNode",
    "HierarchyPlan",
    "OrganizeDeps",
    "IncrementalOrganizeDeps",
    "build_organize_agent",
    "build_incremental_organize_agent",
    "propose_hierarchy",
    "propose_incremental_hierarchy",
]
