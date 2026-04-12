"""Policy vertical context — regulatory and compliance classification."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class PolicyContext(BaseModel):
    """Policy vertical — regulatory, compliance, and governance content."""

    model_config = ConfigDict(extra="forbid")

    DESCRIPTION: ClassVar[str] = (
        "Policy and regulatory content: government regulations, compliance guidelines, "
        "executive orders, departmental memoranda, administrative circulars, "
        "governance frameworks, institutional policies."
    )
    BASE_INSTRUCTION: ClassVar[str] = (
        "Write with regulatory precision. Identify the issuing authority, effective "
        "dates, and scope of applicability. Distinguish between mandatory requirements "
        "and advisory guidance. Note superseded or amended provisions."
    )
    VERTICAL_KEY: ClassVar[str] = "policy"

    jurisdiction: str = Field(
        default="unknown", description="Governing body or region (e.g. PH, US-FDA, EU)."
    )
    policy_version: str = Field(
        default="unknown", description="Policy version or year."
    )
    status: str = Field(
        default="active",
        description="Policy status: active, draft, superseded, archived.",
    )
