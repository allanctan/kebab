"""Legal vertical context — jurisprudence classification."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class LegalContext(BaseModel):
    """Legal vertical — statutes, case law, and legal analysis."""

    model_config = ConfigDict(extra="forbid")

    DESCRIPTION: ClassVar[str] = (
        "Legal content: court decisions, jurisprudence, statutes, case law, "
        "legal opinions, Supreme Court rulings, appellate decisions, "
        "legal commentary, law review articles."
    )
    BASE_INSTRUCTION: ClassVar[str] = (
        "Write with legal precision. Identify the court, case number, and date. "
        "Extract the key legal issues, the court's holding, and the dispositive "
        "portion verbatim when available. Distinguish between ratio decidendi "
        "(binding reasoning) and obiter dicta (non-binding remarks). Cite "
        "relevant statutes and precedents referenced in the decision."
    )
    VERTICAL_KEY: ClassVar[str] = "legal"

    jurisdiction: str = Field(
        default="unknown", description="Legal jurisdiction (e.g. PH, US-Federal, UK)."
    )
    area_of_law: str = Field(
        default="general", description="Primary area of law (e.g. criminal, civil, labor)."
    )
    authority: str = Field(
        default="commentary",
        description="Source authority: statute, case_law, regulation, commentary, opinion.",
    )
    year: int | None = Field(
        default=None, description="Year of enactment or decision, if known."
    )
    decision: str | None = Field(
        default=None, description="The court's ruling (e.g. affirmed, reversed, dismissed).",
    )
    dispositive_portion: str | None = Field(
        default=None, description="The operative part of the judgment that orders specific actions.",
    )
