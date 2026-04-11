"""Confidence and verification models. Universal across all verticals."""

from datetime import date as _date
from typing import Literal

from pydantic import BaseModel, Field

ConfidenceLevel = Literal[0, 1, 2, 3, 4]
"""Confidence gate:

0. No source
1. Has sources
2. 1 LLM verified
3. 2+ LLMs verified, 2+ sources (production gate)
4. Human verified
"""


class VerificationRecord(BaseModel):
    """Result of a single LLM verification pass, stored in frontmatter."""

    model: str = Field(..., description="Verifier model identifier, e.g. 'gpt-4o'.")
    passed: bool = Field(..., description="Whether the model judged the article grounded.")
    date: _date = Field(..., description="Date the verification ran.")
    notes: str | None = Field(default=None, description="Optional model notes.")
