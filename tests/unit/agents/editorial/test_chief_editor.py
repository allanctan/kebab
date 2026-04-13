from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.editorial.chief_editor import (
    ChiefEditorDeps,
    ClaimRewrite,
    UnresolvableDispute,
    Verdict,
    _build_user_prompt,
)


class TestVerdict:
    def test_accept_verdict_with_no_rewrites(self) -> None:
        v = Verdict(
            decision="accept",
            rewrites=[],
            unresolvable=[],
            unconfirmed_claims=[],
            reasoning="All claims confirmed, no disputes remaining.",
        )
        assert v.decision == "accept"

    def test_loop_verdict_with_rewrites_and_unresolvable(self) -> None:
        v = Verdict(
            decision="loop",
            rewrites=[
                ClaimRewrite(
                    original_claim="Plates move at 10cm/year",
                    corrected_claim="Plates move at 2-15cm/year",
                    source_url="https://britannica.com/plate-tectonics",
                    reasoning="Britannica gives a range, not a single value.",
                )
            ],
            unresolvable=[
                UnresolvableDispute(
                    claim="Convection is the primary driver",
                    reasoning="Contested among geologists.",
                )
            ],
            unconfirmed_claims=["Subduction causes earthquakes"],
            reasoning="One unconfirmed claim remains.",
        )
        assert v.decision == "loop"
        assert len(v.rewrites) == 1
        assert len(v.unresolvable) == 1

    def test_invalid_decision_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Verdict(
                decision="maybe",
                rewrites=[],
                unresolvable=[],
                unconfirmed_claims=[],
                reasoning="test",
            )

    def test_normalizes_decision_case(self) -> None:
        v = Verdict(
            decision="ACCEPT",
            rewrites=[],
            unresolvable=[],
            unconfirmed_claims=[],
            reasoning="Done.",
        )
        assert v.decision == "accept"


class TestBuildUserPrompt:
    def test_includes_article_id_and_cycle(self) -> None:
        deps = ChiefEditorDeps(
            settings=None,  # type: ignore[arg-type]
            article_id="SCI-ESC-003",
            article_body="Some body text.",
            frontmatter={"research_claims_total": 10, "external_confirms": 8},
            disputes_section="- **Claim**: test",
            gaps_section="- Unanswered gap?",
            audit_entries=[],
            authoritative_sources=["britannica.com"],
            cycle=2,
            max_cycles=3,
        )
        prompt = _build_user_prompt(deps)
        assert "SCI-ESC-003" in prompt
        assert "Cycle 2 of 3" in prompt
        assert "britannica.com" in prompt
        assert "research_claims_total: 10" in prompt

    def test_truncates_long_body_preserving_sections(self) -> None:
        long_body = "x" * 10000 + "\n\n## Disputes\n\n- **Claim**: important"
        deps = ChiefEditorDeps(
            settings=None,  # type: ignore[arg-type]
            article_id="TEST",
            article_body=long_body,
            frontmatter={},
            disputes_section="",
            gaps_section="",
            audit_entries=[],
            authoritative_sources=[],
            cycle=1,
            max_cycles=3,
        )
        prompt = _build_user_prompt(deps)
        assert "truncated" in prompt
        assert "important" in prompt

    def test_shows_no_disputes_placeholder(self) -> None:
        deps = ChiefEditorDeps(
            settings=None,  # type: ignore[arg-type]
            article_id="TEST",
            article_body="Body.",
            frontmatter={},
            disputes_section="",
            gaps_section="",
            audit_entries=[],
            authoritative_sources=[],
            cycle=1,
            max_cycles=3,
        )
        prompt = _build_user_prompt(deps)
        assert "(no disputes)" in prompt
        assert "(no gaps)" in prompt

    def test_limits_audit_entries(self) -> None:
        entries = [{"action": f"act_{i}", "detail": f"d_{i}"} for i in range(30)]
        deps = ChiefEditorDeps(
            settings=None,  # type: ignore[arg-type]
            article_id="TEST",
            article_body="Body.",
            frontmatter={},
            disputes_section="",
            gaps_section="",
            audit_entries=entries,
            authoritative_sources=[],
            cycle=1,
            max_cycles=3,
        )
        prompt = _build_user_prompt(deps)
        # Only last 20 entries should appear
        assert "act_10" in prompt
        assert "act_29" in prompt
