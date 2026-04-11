"""Education vertical context — K-12 classification."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class EducationContext(BaseModel):
    """K-12 education context."""

    model_config = ConfigDict(extra="forbid")

    SYSTEM_PROMPT: ClassVar[str] = (
        "You classify educational articles by grade level and subject.\n\n"
        "## Input\n"
        "- `article_name`: title of the article.\n"
        "- `body_excerpt`: first ~2000 chars of the article body.\n"
        "- `source_metadata`: metadata extracted from source file paths. "
        "If `grade` and `subject` fields are present, use them directly.\n\n"
        "## Output\n"
        "- `grade`: integer 1–12 — the recommended grade for this material.\n"
        "- `subject`: academic subject (e.g. 'science', 'mathematics', 'english').\n"
        "- `language`: ISO 639-1 code (default \"en\").\n\n"
        "If source_metadata provides grade and subject, use those exact values. "
        "Otherwise, infer from the article content."
    )
    VERTICAL_KEY: ClassVar[str] = "education"

    grade: int = Field(..., ge=1, le=12, description="Recommended K-12 grade level.")
    subject: str = Field(..., description="Academic subject (e.g. science, mathematics, english).")
    language: str = Field(default="en", description="ISO 639-1 language code.")
