"""Curriculum spine models — competency list + coverage index.

A *curriculum spine* is a structured list of learning competencies
(LCs) that articles are tagged against. KEBAB stores one spine YAML
per curriculum under ``knowledge/.kebab/curriculum/<name>.yaml`` and a
derived coverage JSON next to it.

See ``docs/superpowers/specs/2026-05-22-deped-curriculum-spine-design.md``.
"""

from __future__ import annotations

from datetime import date as date_type

from pydantic import BaseModel, ConfigDict, Field


class Competency(BaseModel):
    """One learning competency from the curriculum.

    Fields beyond ``code`` and ``competency`` are optional because the
    upstream XLSX coverage varies by subject/grade.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        ...,
        description="LC code, used as primary key (e.g. 'EN10LIT-I-2c').",
    )
    curriculum: str = Field(
        default="",
        description="Curriculum name (e.g. 'MATATAG', 'K-12 MELC').",
    )
    subject: str = Field(
        default="",
        description="Subject area (e.g. 'Science', 'Mathematics').",
    )
    grade: str = Field(
        default="",
        description="Grade level as string ('10', 'K', etc.).",
    )
    key_stage: str | None = Field(
        default=None,
        description="DepEd Key Stage (e.g. 'KS4' for G9-10).",
    )
    quarter: str | None = Field(
        default=None,
        description="Quarter label (e.g. 'Q1', 'I').",
    )
    domain: str | None = Field(
        default=None,
        description="Curriculum domain (e.g. 'Plate Tectonics').",
    )
    subdomain: str | None = Field(
        default=None,
        description="Finer-grained subdomain if present.",
    )
    competency: str = Field(
        ...,
        description="Full learning-competency sentence.",
    )
    content_standard: str | None = Field(
        default=None,
        description="Content Standard prose, if specified.",
    )
    performance_standard: str | None = Field(
        default=None,
        description="Performance Standard prose, if specified.",
    )
    source_pdf_url: str | None = Field(
        default=None,
        description="DepEd CG PDF URL the competency was sourced from.",
    )


class CurriculumSpine(BaseModel):
    """A complete curriculum spine — competencies plus metadata.

    Serialized to ``knowledge/.kebab/curriculum/<name>.yaml``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        description="Short identifier for this spine (e.g. 'matatag-g10-draft').",
    )
    curriculum: str = Field(
        ...,
        description="Curriculum framework name (e.g. 'MATATAG').",
    )
    source_file: str = Field(
        ...,
        description="Path under knowledge/raw/curriculum/ for the source artifact.",
    )
    generated_at: date_type = Field(
        ...,
        description="Date the spine was built from the source.",
    )
    grade_filter: str | None = Field(
        default=None,
        description="If built for a single grade, the grade string ('10').",
    )
    competencies: list[Competency] = Field(
        default_factory=list,
        description="The full competency list, in source order.",
    )


class SubjectCoverage(BaseModel):
    """Per-subject coverage summary."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(..., description="Total LCs for this subject.")
    covered: int = Field(..., description="LCs with at least one article.")
    uncovered: int = Field(..., description="LCs with no articles.")


class CompetencyCoverage(BaseModel):
    """Reverse index — LC code → article IDs that teach it.

    Serialized to ``knowledge/.kebab/curriculum/<name>.coverage.json``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Spine name this coverage refers to.")
    generated_at: date_type = Field(
        ..., description="Date the coverage index was built."
    )
    total_competencies: int = Field(
        ..., description="Total LCs across all subjects."
    )
    covered: int = Field(..., description="LCs with at least one article.")
    uncovered: int = Field(..., description="LCs with no articles.")
    by_subject: dict[str, SubjectCoverage] = Field(
        default_factory=dict,
        description="Per-subject coverage summary.",
    )
    competencies: dict[str, list[str]] = Field(
        default_factory=dict,
        description="LC code → list of article IDs that teach it.",
    )
