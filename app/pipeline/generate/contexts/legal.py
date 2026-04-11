"""Legal vertical context — jurisprudence classification."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class LegalContext(BaseModel):
    """Legal vertical — statutes, case law, and legal analysis."""

    model_config = ConfigDict(extra="forbid")

    SYSTEM_PROMPT: ClassVar[str] = (
        "You classify legal articles by jurisdiction, area of law, and authority.\n\n"
        "## Input\n"
        "- `article_name`: title of the article.\n"
        "- `body_excerpt`: first ~2000 chars of the article body.\n\n"
        "## Output\n"
        "- `jurisdiction`: legal jurisdiction (e.g. 'PH', 'US-Federal', 'UK', 'EU').\n"
        "- `area_of_law`: primary area (e.g. 'criminal', 'civil', 'labor', "
        "'constitutional', 'commercial', 'environmental').\n"
        "- `authority`: source authority — one of 'statute', 'case_law', "
        "'regulation', 'commentary', 'opinion'.\n"
        "- `year`: year of enactment or decision, if identifiable. Use null if unknown.\n"
        "- `decision`: the court's ruling (e.g. 'affirmed', 'reversed', 'dismissed'). Null if not a case.\n"
        "- `dispositive_portion`: the operative part of the judgment that orders specific actions. "
        "Extract verbatim if available. Null if not a case.\n\n"
        "Infer jurisdiction from the content. If unclear, use 'unknown'."
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
        default=None, description="The court's decision or ruling (e.g. 'affirmed', 'reversed', 'dismissed').",
    )
    dispositive_portion: str | None = Field(
        default=None, description="The dispositive portion — the operative part of the judgment that orders specific actions.",
    )
