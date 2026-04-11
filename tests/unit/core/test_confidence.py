"""Confidence ladder transitions per spec §7."""

from __future__ import annotations

from datetime import date

from app.core.confidence import compute_confidence
from app.models.confidence import VerificationRecord
from app.models.frontmatter import FrontmatterSchema
from app.models.source import Source

TODAY = date(2026, 4, 9)


def _fm(**kw: object) -> FrontmatterSchema:
    base: dict[str, object] = {
        "id": "X-1",
        "name": "X",
        "type": "article",
        "sources": [],
        "verifications": [],
        "human_verified": False,
    }
    base.update(kw)
    return FrontmatterSchema.model_validate(base)


def _source(title: str = "src") -> Source:
    return Source(id=0, title=title, tier=2)


def _verif(passed: bool, model: str = "google-gla:gemini-2.5-flash") -> VerificationRecord:
    return VerificationRecord(model=model, passed=passed, date=TODAY)


def test_level_0_when_no_sources() -> None:
    assert compute_confidence(_fm()) == 0


def test_level_1_when_sources_but_no_verifications() -> None:
    assert compute_confidence(_fm(sources=[_source()])) == 1


def test_level_1_when_only_failed_verifications() -> None:
    assert (
        compute_confidence(_fm(sources=[_source()], verifications=[_verif(False)])) == 1
    )


def test_level_2_when_one_verifier_passed() -> None:
    assert (
        compute_confidence(_fm(sources=[_source()], verifications=[_verif(True)])) == 2
    )


def test_level_2_when_two_verifiers_but_only_one_source() -> None:
    fm = _fm(
        sources=[_source()],
        verifications=[_verif(True, "a"), _verif(True, "b")],
    )
    assert compute_confidence(fm) == 2


def test_level_3_when_two_verifiers_and_two_sources() -> None:
    fm = _fm(
        sources=[_source("a"), _source("b")],
        verifications=[_verif(True, "m1"), _verif(True, "m2")],
    )
    assert compute_confidence(fm) == 3


def test_level_4_when_human_verified_overrides_everything() -> None:
    assert compute_confidence(_fm(human_verified=True)) == 4
    assert compute_confidence(_fm(sources=[_source()], human_verified=True)) == 4
