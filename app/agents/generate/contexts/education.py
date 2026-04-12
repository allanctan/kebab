"""Education vertical context — K-12 classification."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Bloom's revised taxonomy levels (lowercase canonical form).
BloomLevel = Literal[
    "remember", "understand", "apply", "analyze", "evaluate", "create"
]

# Webb's Depth of Knowledge levels.
DokLevel = Literal[1, 2, 3, 4]


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
        "Write for grade {grade} {subject} students. Use clear, "
        "age-appropriate language. Structure the article as: introduction "
        "defining the topic scope, then core content organized by concept, "
        "then a brief summary of key points. Define technical terms on "
        "first use."
    )
    VERTICAL_KEY: ClassVar[str] = "education"

    grade: int = Field(..., ge=1, le=12, description="Recommended K-12 grade level.")
    subject: str = Field(..., description="Academic subject (e.g. science, mathematics, english).")
    language: str = Field(default="en", description="ISO 639-1 language code.")
    learning_objectives: list[str] = Field(
        default_factory=list,
        description="What the student should be able to do after studying this article. "
        "Each objective starts with an action verb (e.g. 'Explain the three types of plate boundaries').",
    )
    bloom_level: BloomLevel = Field(
        default="understand",
        description="Highest Bloom's revised taxonomy level the article reaches: "
        "remember, understand, apply, analyze, evaluate, create.",
    )
    dok_level: DokLevel = Field(
        default=2,
        description="Webb's Depth of Knowledge level (1=Recall, 2=Skill/Concept, "
        "3=Strategic Thinking, 4=Extended Thinking).",
    )
    concept_tags: list[str] = Field(
        default_factory=list,
        description="Key technical terms and domain vocabulary introduced or "
        "used in the article (e.g. 'lithosphere', 'subduction', "
        "'asthenosphere'). These are the terms a student must learn, "
        "not broad topic labels. 5-15 tags.",
    )

    @field_validator("bloom_level", mode="before")
    @classmethod
    def _normalize_bloom(cls, v: object) -> object:
        """LLMs return 'Understand', 'APPLY', etc. — normalize to lowercase."""
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("subject", mode="before")
    @classmethod
    def _normalize_subject(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower()
        return v
