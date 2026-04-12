"""Education vertical context — K-12 classification."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class EducationContext(BaseModel):
    """K-12 education context."""

    model_config = ConfigDict(extra="forbid")

    DESCRIPTION: ClassVar[str] = (
        "Educational content: textbooks, curriculum modules, learning materials, "
        "lesson plans, school subjects including science, earth science, physics, "
        "biology, chemistry, mathematics, algebra, geometry, english, language arts, "
        "social studies, history, geography. Any K-12 or college academic material."
    )
    BASE_INSTRUCTION: ClassVar[str] = (
        "Write for students at the specified grade level. Use clear, age-appropriate "
        "language. Include concrete examples and visual descriptions. Structure with "
        "learning objectives, core content, and review questions in mind."
    )
    VERTICAL_KEY: ClassVar[str] = "education"

    grade: int = Field(..., ge=1, le=12, description="Recommended K-12 grade level.")
    subject: str = Field(..., description="Academic subject (e.g. science, mathematics, english).")
    language: str = Field(default="en", description="ISO 639-1 language code.")
