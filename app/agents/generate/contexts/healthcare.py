"""Healthcare vertical context — clinical content classification."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class HealthcareContext(BaseModel):
    """Healthcare vertical — clinical and medical content."""

    model_config = ConfigDict(extra="forbid")

    DESCRIPTION: ClassVar[str] = (
        "Healthcare and medical content: clinical guidelines, drug information, "
        "patient education, disease management, public health advisories, "
        "medical research summaries, nursing protocols."
    )
    BASE_INSTRUCTION: ClassVar[str] = (
        "Write with clinical precision. Cite evidence grades where applicable. "
        "Distinguish between established guidelines and emerging research. "
        "Include contraindications and safety considerations. Use standard "
        "medical terminology with plain-language explanations for patient-facing content."
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
