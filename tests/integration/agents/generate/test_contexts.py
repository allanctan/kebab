"""Contexts stage with stubbed proposer."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.config import Settings
from app.core.markdown import read_article
from app.agents.generate import contexts as contexts_stage


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    biology = knowledge / "curated" / "Science" / "Biology"
    biology.mkdir(parents=True)
    (biology / "photo.md").write_text(
        "---\nid: SCI-BIO-001\nname: Photosynthesis\ntype: article\n"
        "sources:\n  - id: 0\n    title: x\n    tier: 2\n---\n\n"
        "# Photosynthesis\n\nLight reactions and Calvin cycle.\n",
        encoding="utf-8",
    )
    (knowledge / "raw" / "documents").mkdir(parents=True)
    return Settings(
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        CURATED_DIR=knowledge / "curated",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )


def _stub_proposer(
    _settings: Settings, deps: contexts_stage.ContextDeps, _cls: type
) -> contexts_stage.EducationContext:
    # Use grade/subject from source metadata if available, otherwise default.
    grade = 7
    subject = "science"
    for meta in deps.source_metadata:
        if "grade" in meta:
            grade = int(meta["grade"])
        if "subject" in meta:
            subject = meta["subject"]
    return contexts_stage.EducationContext(grade=grade, subject=subject, language="en")


@pytest.mark.integration
def test_contexts_writes_grade_into_frontmatter(settings: Settings) -> None:
    result = contexts_stage.run(settings, proposer=_stub_proposer)
    assert len(result.updated) == 1
    fm, _, _ = read_article(result.updated[0])
    contexts = fm.model_dump().get("contexts") or {}
    assert contexts["education"] == {"grade": 7, "subject": "science", "language": "en"}


@pytest.mark.integration
def test_contexts_walk_scoped_to_curated(settings: Settings) -> None:
    """Markdown outside CURATED_DIR must never be touched."""
    raw_md = settings.KNOWLEDGE_DIR / "raw" / "stray.md"
    raw_md.write_text(
        "---\nid: X-1\nname: x\ntype: article\nsources: []\n---\n\nbody",
        encoding="utf-8",
    )
    result = contexts_stage.run(settings, proposer=_stub_proposer)
    assert raw_md not in result.updated
    # The stray file is unchanged (no contexts block added).
    assert "contexts" not in raw_md.read_text()


@pytest.mark.integration
def test_contexts_records_proposer_failures_as_skipped(settings: Settings) -> None:
    def _fail(_s: Settings, _d: contexts_stage.ContextDeps, _cls: type) -> contexts_stage.EducationContext:  # noqa: ARG001
        raise RuntimeError("model unavailable")

    result = contexts_stage.run(settings, proposer=_fail)
    assert result.updated == []
    assert len(result.skipped) == 1
    assert "model unavailable" in result.skipped[0][1]
