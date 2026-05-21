"""Curriculum agent — ingest DepEd curriculum guides, build coverage.

Pure-Python plumbing: no LLM calls. Parses XLSX competency lists into
:class:`~app.models.curriculum.CurriculumSpine` artifacts and walks the
curated tree to build :class:`~app.models.curriculum.CompetencyCoverage`
indices.

See ``docs/superpowers/specs/2026-05-22-deped-curriculum-spine-design.md``.
"""

from __future__ import annotations

from app.agents.curriculum.curriculum import (
    IngestResult as IngestResult,
    ingest_xlsx as ingest_xlsx,
)
from app.agents.curriculum.coverage import (
    build_coverage as build_coverage,
)
from app.agents.curriculum.tagger import (
    ArticleTagOutcome as ArticleTagOutcome,
    tag_articles as tag_articles,
)
