"""Article model — the 17-field universal Qdrant payload.

This schema is the same for every vertical and never changes per domain.
See spec §4 (kebab-knowledge-base-architecture.html).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.confidence import ConfidenceLevel
from app.models.context import ContextMapping

LevelType = Literal["domain", "subdomain", "topic", "article"]


class Article(BaseModel):
    """Universal Qdrant payload. 17 fields, no vertical extensions."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Universal article ID, e.g. 'SCI-BIO-002'.")
    name: str = Field(..., description="Article name.")
    description: str = Field(..., description="One- or two-sentence summary.")
    keywords: list[str] = Field(
        default_factory=list, description="Key topics; enriches the embedding."
    )
    faq: list[str] = Field(
        default_factory=list,
        description="Questions extracted from the markdown ## Q&A section.",
    )
    level_type: LevelType = Field(..., description="Hierarchy level.")
    parent_ids: list[str] = Field(
        default_factory=list, description="Parent node IDs (DAG)."
    )
    depth: int = Field(..., description="Hierarchy depth from root.")
    position: int = Field(default=0, description="Sibling ordering.")
    domain: str = Field(..., description="Top-level domain name.")
    subdomain: str | None = Field(default=None, description="Second-level domain name.")
    prerequisites: list[str] = Field(
        default_factory=list, description="Prerequisite article IDs."
    )
    related: list[str] = Field(
        default_factory=list, description="Related article IDs."
    )
    md_path: str | None = Field(
        default=None, description="Pointer to the .md file on disk."
    )
    confidence_level: ConfidenceLevel = Field(
        ..., description="Computed during sync from sources + verifications."
    )
    contexts: ContextMapping = Field(
        default_factory=ContextMapping,
        description="Nested vertical-specific context for filtering.",
    )
    # The 17th field — embedding — is stored as the Qdrant vector, not in the payload.
