"""Article model — the 12-field universal Qdrant payload.

This schema is the same for every vertical and never changes per domain.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.confidence import ConfidenceLevel
from app.models.context import ContextMapping


class Article(BaseModel):
    """Universal Qdrant payload. 12 fields, no vertical extensions."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Universal article ID, e.g. 'SCI-BIO-002'.")
    name: str = Field(..., description="Article name.")
    description: str = Field(..., description="One- or two-sentence summary.")
    keywords: list[str] = Field(
        default_factory=list, description="Key topics; enriches the embedding."
    )
    parent_ids: list[str] = Field(
        default_factory=list, description="Parent node IDs (DAG)."
    )
    depth: int = Field(..., description="Hierarchy depth from root.")
    domain: str = Field(..., description="Top-level domain name.")
    subdomain: str | None = Field(default=None, description="Second-level domain name.")
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
    # The 12th field — embedding — is stored as the Qdrant vector, not in the payload.
