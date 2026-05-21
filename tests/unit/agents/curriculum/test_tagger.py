"""Tagger tests — subject filter, frontmatter write, idempotency."""

from __future__ import annotations

from pathlib import Path

from app.agents.curriculum.curriculum import ingest_xlsx
from app.agents.curriculum.tagger import (
    TagDeps,
    TagResult,
    tag_articles,
)
from app.config.config import Settings
from app.core.markdown import read_article


def _mk_settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    (knowledge / "curated" / "Knowledge" / "Science").mkdir(parents=True)
    (knowledge / "curated" / "Knowledge" / "Mathematics").mkdir(parents=True)
    return Settings(  # type: ignore[arg-type]
        KNOWLEDGE_DIR=knowledge,
        RAW_DIR=knowledge / "raw",
        PROCESSED_DIR=knowledge / "processed",
        CURATED_DIR=knowledge / "curated",
    )


def _write_article(
    path: Path,
    article_id: str,
    subject: str | None,
    body_text: str = "Body.",
    existing_codes: list[str] | None = None,
) -> None:
    """Write a minimal curated article with optional contexts.education.subject."""
    contexts_block = ""
    if subject:
        contexts_block = f"""contexts:
  education:
    subject: {subject}
    grade: 10
"""
    codes_block = ""
    if existing_codes is not None:
        codes_block = f"competency_codes: {existing_codes}\n"
    body = f"""---
id: {article_id}
name: {article_id}
type: article
sources:
  - id: 1
    title: Test Source
    tier: 1
{codes_block}{contexts_block}---
# {article_id}

{body_text}
"""
    path.write_text(body, encoding="utf-8")


class TestSubjectFilter:
    def test_narrows_candidates_to_article_subject(
        self, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        settings = _mk_settings(tmp_path)
        ingest_xlsx(
            settings, xlsx_path=sample_xlsx, name="test-spine", grade_filter="10"
        )
        sci_path = Path(settings.CURATED_DIR) / "Knowledge" / "Science" / "x.md"
        _write_article(sci_path, "SCI-001", subject="science")

        captured: list[TagDeps] = []

        def fake_proposer(_settings: Settings, deps: TagDeps) -> TagResult:
            captured.append(deps)
            return TagResult(competency_codes=[], reasoning="stub")

        tag_articles(settings, name="test-spine", proposer=fake_proposer)
        assert len(captured) == 1
        candidate_subjects = {c.subject for c in captured[0].candidate_competencies}
        assert candidate_subjects == {"Science"}

    def test_passes_all_candidates_when_subject_unknown(
        self, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        settings = _mk_settings(tmp_path)
        ingest_xlsx(
            settings, xlsx_path=sample_xlsx, name="test-spine", grade_filter="10"
        )
        path = Path(settings.CURATED_DIR) / "Knowledge" / "Science" / "x.md"
        _write_article(path, "MIXED-001", subject=None)

        captured: list[TagDeps] = []

        def fake_proposer(_settings: Settings, deps: TagDeps) -> TagResult:
            captured.append(deps)
            return TagResult(competency_codes=[], reasoning="stub")

        tag_articles(settings, name="test-spine", proposer=fake_proposer)
        # All 3 LCs in the G10 fixture, regardless of subject
        candidate_codes = {c.code for c in captured[0].candidate_competencies}
        assert candidate_codes == {"SCI10-PT-I-1", "SCI10-PT-I-2", "EN10LIT-I-1"}


class TestFrontmatterWrite:
    def test_writes_codes_and_curriculum(
        self, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        settings = _mk_settings(tmp_path)
        ingest_xlsx(
            settings, xlsx_path=sample_xlsx, name="test-spine", grade_filter="10"
        )
        path = Path(settings.CURATED_DIR) / "Knowledge" / "Science" / "x.md"
        _write_article(path, "SCI-001", subject="science")

        def fake_proposer(_settings: Settings, deps: TagDeps) -> TagResult:
            return TagResult(
                competency_codes=["SCI10-PT-I-1", "SCI10-PT-I-2"],
                reasoning="both covered",
            )

        outcomes = tag_articles(
            settings, name="test-spine", proposer=fake_proposer
        )
        assert len(outcomes) == 1
        assert outcomes[0].new_codes == ["SCI10-PT-I-1", "SCI10-PT-I-2"]

        fm, _, _ = read_article(path)
        data = fm.model_dump()
        assert data["competency_codes"] == ["SCI10-PT-I-1", "SCI10-PT-I-2"]
        assert data["curriculum"] == "test-spine"

    def test_rejects_invented_codes(
        self, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        """If the LLM hallucinates a code, it must be dropped."""
        settings = _mk_settings(tmp_path)
        ingest_xlsx(
            settings, xlsx_path=sample_xlsx, name="test-spine", grade_filter="10"
        )
        path = Path(settings.CURATED_DIR) / "Knowledge" / "Science" / "x.md"
        _write_article(path, "SCI-001", subject="science")

        def fake_proposer(_settings: Settings, deps: TagDeps) -> TagResult:
            return TagResult(
                competency_codes=["SCI10-PT-I-1", "FAKE-CODE-99"],
                reasoning="one real, one made up",
            )

        outcomes = tag_articles(
            settings, name="test-spine", proposer=fake_proposer
        )
        # FAKE-CODE-99 dropped; only real candidate code remains
        assert outcomes[0].new_codes == ["SCI10-PT-I-1"]

    def test_idempotent_retag_overwrites(
        self, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        settings = _mk_settings(tmp_path)
        ingest_xlsx(
            settings, xlsx_path=sample_xlsx, name="test-spine", grade_filter="10"
        )
        path = Path(settings.CURATED_DIR) / "Knowledge" / "Science" / "x.md"
        _write_article(
            path, "SCI-001", subject="science", existing_codes=["SCI10-PT-I-2"]
        )

        def first(_settings: Settings, _deps: TagDeps) -> TagResult:
            return TagResult(competency_codes=["SCI10-PT-I-1"], reasoning="r1")

        out1 = tag_articles(settings, name="test-spine", proposer=first)
        assert out1[0].previous_codes == ["SCI10-PT-I-2"]
        assert out1[0].new_codes == ["SCI10-PT-I-1"]

        def second(_settings: Settings, _deps: TagDeps) -> TagResult:
            return TagResult(
                competency_codes=["SCI10-PT-I-1", "SCI10-PT-I-2"], reasoning="r2"
            )

        out2 = tag_articles(settings, name="test-spine", proposer=second)
        assert out2[0].previous_codes == ["SCI10-PT-I-1"]
        assert out2[0].new_codes == ["SCI10-PT-I-1", "SCI10-PT-I-2"]


class TestSelection:
    def test_domain_filter_restricts_walk(
        self, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        settings = _mk_settings(tmp_path)
        ingest_xlsx(
            settings, xlsx_path=sample_xlsx, name="test-spine", grade_filter="10"
        )
        sci = Path(settings.CURATED_DIR) / "Knowledge" / "Science" / "x.md"
        math = Path(settings.CURATED_DIR) / "Knowledge" / "Mathematics" / "y.md"
        _write_article(sci, "SCI-001", subject="science")
        _write_article(math, "MATH-001", subject="mathematics")

        def empty(_settings: Settings, _deps: TagDeps) -> TagResult:
            return TagResult(competency_codes=[], reasoning="")

        outcomes = tag_articles(
            settings,
            name="test-spine",
            domain="Knowledge/Science",
            proposer=empty,
        )
        assert {o.article_id for o in outcomes} == {"SCI-001"}

    def test_article_id_targets_single_file(
        self, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        settings = _mk_settings(tmp_path)
        ingest_xlsx(
            settings, xlsx_path=sample_xlsx, name="test-spine", grade_filter="10"
        )
        sci = Path(settings.CURATED_DIR) / "Knowledge" / "Science" / "x.md"
        math = Path(settings.CURATED_DIR) / "Knowledge" / "Mathematics" / "y.md"
        _write_article(sci, "SCI-001", subject="science")
        _write_article(math, "MATH-001", subject="mathematics")

        def empty(_settings: Settings, _deps: TagDeps) -> TagResult:
            return TagResult(competency_codes=[], reasoning="")

        outcomes = tag_articles(
            settings, name="test-spine", article_id="MATH-001", proposer=empty
        )
        assert len(outcomes) == 1
        assert outcomes[0].article_id == "MATH-001"
