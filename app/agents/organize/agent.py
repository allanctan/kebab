"""Pydantic-ai Agent that proposes a knowledge hierarchy from raw documents.

This is **not** a "kebab agent" in the spec sense (those live under
``app/agents/``); it is an LLM helper used by the organize stage. Kept
in ``app/pipeline/`` alongside the organize stage that calls it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, RunContext

from app.config.config import Settings
from app.core.llm.resolve import resolve_model

logger = logging.getLogger(__name__)


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


_SYSTEM_PROMPT = """You are an information architect organizing source documents into a learning knowledge base.

## Input
- `domain_hint`: A short label for the top-level domain (e.g. "Science").
- `manifest`: A list of (filename, first 2000 chars) tuples for every raw document.

## Output
- `nodes`: A flat list of HierarchyNode entries forming a tree via `parent_id`.
  - Exactly one node should have `level_type="domain"` and `parent_id=None`.
  - Subdomains, topics, and articles must reference an existing parent.
  - IDs follow the pattern `<DOMAIN_PREFIX>-<SUBDOMAIN_PREFIX>-<NNN>` (3-letter caps).

## Source attribution (important)
- For each `article` node, list the **source IDs** (integers) from the manifest
  that discuss the topic — not just the primary source. Multi-source coverage is
  the foundation of the confidence gate (articles with ≥2 sources can reach
  confidence 3 after verification). Include a source if it corroborates,
  contextualizes, or cross-references the article's topic.
- Minimum: 1 source. Prefer 2–4 when the manifest supports it.
- `source_files` must contain integers matching the [N] IDs shown in the manifest.

Constraints:
- Do not invent sources or content beyond what is in the manifest.
- **Only include sources that genuinely belong to the domain.** If the domain_hint
  is "Science", do not include mathematics, literature, or other unrelated subjects.
  Skip manifest entries that don't fit — it is better to exclude than to force-fit.
- Prefer 1 domain → 1–3 subdomains → 2–6 topics → 4–10 articles per topic.
"""


_INCREMENTAL_SYSTEM_PROMPT = """You are an information architect extending an EXISTING knowledge-base hierarchy with NEW source documents.

## Input
- `existing_tree`: Summary of the current hierarchy (id, level_type, name, description) for every node already in the plan.
- `new_manifest`: `[(filename, snippet), …]` for sources NOT yet in the plan.

## What to return
A `HierarchyPlan` whose `nodes` list contains ONLY the delta — two kinds of entries:

1. **Extensions to existing articles.** For each new source that corroborates
   an already-existing article, emit a node whose `id` EXACTLY MATCHES the
   existing article's id, with the same `level_type`, `parent_id`, `name`,
   `description`, and `source_files=[<new_source_id>]`. The merge step will
   union the new source IDs into the existing article's `source_files`.
   `source_files` must contain integers matching the [N] IDs shown in the
   new_manifest.

2. **Brand-new articles (and their ancestors if missing).** For each
   genuinely new topic, emit one `article` node with a fresh id that does
   NOT clash with any existing id, and — if its parent topic/subdomain/domain
   does not yet exist in `existing_tree` — emit those ancestor nodes as well
   with fresh ids. Fresh ids must follow the `<DOMAIN>-<SUBDOMAIN>-<NNN>`
   pattern, picking the next free `NNN` for each branch.

## Rules
- NEVER rename an existing node or change its parent_id.
- NEVER emit `md_path` (the organize stage sets it).
- NEVER invent content beyond the new_manifest.
- If a new source is purely a duplicate of material already covered by an
  existing article, attach it to that article — do NOT create a new one.
- Prefer extensions over new articles when the topic overlaps.
- The emitted plan may be empty if no new coverage is warranted (e.g. every
  new source is a near-duplicate that adds no new articles and the operator
  already has perfect coverage).
"""


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
