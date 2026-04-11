"""Source citation model. Universal across all verticals."""

from datetime import date as _date
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SourceTier = Literal[1, 2, 3, 4, 5]
"""Publisher authority tier:

1. Official / authoritative (e.g. statute, DepEd MELC, DOH CPG)
2. Peer-reviewed / published (e.g. OpenStax, ISO standard)
3. Academic / expert (e.g. journal articles, Cochrane)
4. Reputable platform (e.g. Khan Academy, UpToDate)
5. General / community (e.g. Wikipedia, blogs)
"""


class Source(BaseModel):
    """A single grounded source cited by an article."""

    model_config = ConfigDict(extra="allow")

    id: int = Field(default=0, description="Source index ID. Default 0 for LLM-generated citations.")
    title: str = Field(..., description="Human-readable title of the source.")
    url: str | None = Field(default=None, description="Canonical URL if available.")
    tier: SourceTier = Field(..., description="Publisher authority tier (1–5).")
    evidence_grade: str | None = Field(
        default=None,
        description="Optional research-quality grade (healthcare GRADE, etc.).",
    )
    study_type: str | None = Field(
        default=None,
        description="Optional study type (e.g. systematic_review, RCT, case_report).",
    )
    # M17 provenance envelope — all optional so pre-M17 articles keep validating.
    author: str | None = Field(
        default=None,
        description="Primary author or organization responsible for the source.",
    )
    published_date: _date | None = Field(
        default=None,
        description="Upstream publication date, if known.",
    )
    retrieved_at: datetime | None = Field(
        default=None,
        description="Timestamp when KEBAB fetched the source.",
    )
    license: str | None = Field(
        default=None,
        description="Upstream license identifier (e.g. 'CC-BY-4.0', 'public-domain').",
    )
    checksum: str | None = Field(
        default=None,
        description="SHA256 hex digest of the raw bytes, used for dedup across adapters.",
    )
    adapter: str | None = Field(
        default=None,
        description="Name of the source adapter that fetched this source.",
    )
