"""Healthcare vertical context — clinical content classification."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class HealthcareContext(BaseModel):
    """Healthcare vertical — clinical and medical content."""

    model_config = ConfigDict(extra="forbid")

    SYSTEM_PROMPT: ClassVar[str] = (
        "You classify healthcare articles by evidence grade and specialty.\n\n"
        "## Input\n"
        "- `article_name`: title of the article.\n"
        "- `body_excerpt`: first ~2000 chars of the article body.\n\n"
        "## Output\n"
        "- `evidence_grade`: GRADE scale — one of 'high', 'moderate', 'low', 'very_low'.\n"
        "- `specialty`: medical specialty (e.g. 'cardiology', 'oncology', 'general').\n"
        "- `audience`: intended reader — one of 'clinician', 'patient', 'researcher'.\n\n"
        "Base evidence_grade on the strength of cited sources: systematic reviews "
        "and RCTs are 'high', observational studies 'moderate', expert opinion 'low'."
    )
    VERTICAL_KEY: ClassVar[str] = "healthcare"

    evidence_grade: str = Field(
        ..., description="GRADE evidence level: high, moderate, low, very_low."
    )
    specialty: str = Field(
        default="general", description="Medical specialty (e.g. cardiology, oncology)."
    )
    audience: str = Field(
        default="clinician",
        description="Intended audience: clinician, patient, or researcher.",
    )
