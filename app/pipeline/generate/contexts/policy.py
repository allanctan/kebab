"""Policy vertical context — regulatory and compliance classification."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class PolicyContext(BaseModel):
    """Policy vertical — regulatory, compliance, and governance content."""

    model_config = ConfigDict(extra="forbid")

    SYSTEM_PROMPT: ClassVar[str] = (
        "You classify policy and regulatory articles by jurisdiction and status.\n\n"
        "## Input\n"
        "- `article_name`: title of the article.\n"
        "- `body_excerpt`: first ~2000 chars of the article body.\n\n"
        "## Output\n"
        "- `jurisdiction`: governing body or region (e.g. 'PH', 'US-FDA', 'EU').\n"
        "- `policy_version`: version or year of the policy (e.g. '2024', 'v3.1').\n"
        "- `status`: one of 'active', 'draft', 'superseded', 'archived'.\n\n"
        "Infer jurisdiction from the content. If unclear, use 'unknown'."
    )
    VERTICAL_KEY: ClassVar[str] = "policy"

    jurisdiction: str = Field(
        default="unknown", description="Governing body or region (e.g. PH, US-FDA, EU)."
    )
    policy_version: str = Field(
        default="unknown", description="Policy version or year."
    )
    status: str = Field(
        default="active",
        description="Policy status: active, draft, superseded, archived.",
    )
