"""FrontmatterSchema — universal YAML frontmatter fields for curated articles.

Vertical-specific fields (``bloom_ceiling``, ``evidence_grade``,
``policy_version``, etc.) pass through untouched via
``ConfigDict(extra="allow")``. KEBAB never reads them — consumers do.

See spec §5 (kebab-knowledge-base-architecture.html).
"""

from datetime import date as date_type

from pydantic import BaseModel, ConfigDict, Field

from app.models.confidence import VerificationRecord
from app.models.source import Source


class FrontmatterSchema(BaseModel):
    """Universal frontmatter fields present in every curated article."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., description="Universal article ID.")
    name: str = Field(..., description="Article name.")
    type: str = Field(..., description="Level type, typically 'article'.")
    sources: list[Source] = Field(
        default_factory=list, description="Grounded source citations."
    )
    verifications: list[VerificationRecord] = Field(
        default_factory=list, description="LLM verification records."
    )
    human_verified: bool = Field(
        default=False, description="True once a domain expert has approved."
    )
    human_verified_by: str | None = Field(
        default=None, description="Name/role of the human reviewer."
    )
    human_verified_at: date_type | None = Field(
        default=None, description="Date of human verification."
    )
