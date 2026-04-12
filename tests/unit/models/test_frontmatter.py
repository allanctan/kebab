from datetime import date

from app.models import FrontmatterSchema, Source, VerificationRecord


def test_frontmatter_universal_fields() -> None:
    fm = FrontmatterSchema(
        id="SCI-BIO-002",
        name="Photosynthesis",
        type="article",
        sources=[Source(id=0, title="OpenStax Biology 2e, Ch. 8", tier=2)],
        verifications=[
            VerificationRecord(model="gpt-4o", passed=True, date=date(2026, 4, 1))
        ],
    )
    assert fm.id == "SCI-BIO-002"
    assert fm.sources[0].tier == 2
    assert fm.verifications[0].passed is True


def test_frontmatter_passes_through_vertical_fields() -> None:
    """Vertical-specific keys like bloom_ceiling must pass through untouched."""
    fm = FrontmatterSchema.model_validate(
        {
            "id": "SCI-BIO-002",
            "name": "Photosynthesis",
            "type": "article",
            "bloom_ceiling": "Analyze",  # education-specific
            "valid_from": "2024-05-01",  # legal-specific
            "policy_version": "3.2",  # corporate-specific
        }
    )
    dumped = fm.model_dump()
    assert dumped["bloom_ceiling"] == "Analyze"
    assert dumped["valid_from"] == "2024-05-01"
    assert dumped["policy_version"] == "3.2"
