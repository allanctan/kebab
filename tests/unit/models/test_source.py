from app.models import Source


def test_source_minimal() -> None:
    s = Source(id=0, title="DepEd MELC Science Grade 7", tier=1)
    assert s.tier == 1
    assert s.url is None


def test_source_with_evidence_grade() -> None:
    s = Source(
        id=0,
        title="Cochrane: Metformin monotherapy for T2D",
        tier=3,
        evidence_grade="high",
        study_type="systematic_review",
    )
    assert s.evidence_grade == "high"
    assert s.study_type == "systematic_review"
