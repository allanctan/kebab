# tests/unit/agents/research_gaps/test_research_gaps_writer.py
"""Tests for writer.apply_answers_to_gaps with per-kind formats."""

from __future__ import annotations

from app.agents.research_gaps.writer import GapAnswer, apply_answers_to_gaps


def _gaps_body() -> str:
    return (
        "# Topic\n\nIntro paragraph.\n\n"
        "## Research Gaps\n\n"
        "- What is the difference between X and Y?\n"
        "- Why does this matter to you?\n"
        "- What is the typhoon damage estimate?\n"
    )


class TestRenderFormats:
    def test_external_citation_unchanged(self) -> None:
        body = _gaps_body()
        ans = [
            GapAnswer(
                gap_idx=0,
                answer_text="X differs from Y because Z.",
                source_kind="external",
                source_title="USGS",
                source_url="https://www.usgs.gov/article",
            )
        ]
        out = apply_answers_to_gaps(body, ["q1", "q2", "q3"], ans)
        assert "(Source: [USGS](https://www.usgs.gov/article))" in out

    def test_ai_synthesis_from_article_format(self) -> None:
        body = _gaps_body()
        ans = [
            GapAnswer(
                gap_idx=0,
                answer_text="X differs from Y because Z.",
                source_kind="ai_article",
                answerer_model="claude-opus-4.7",
                verifier_model="gemini-2.5-pro",
            )
        ]
        out = apply_answers_to_gaps(body, ["q1", "q2", "q3"], ans)
        assert (
            "(AI synthesis from article body — answered by "
            "claude-opus-4.7, verified by gemini-2.5-pro — verify before "
            "classroom use)" in out
        )

    def test_ai_synthesis_from_general_format(self) -> None:
        body = _gaps_body()
        ans = [
            GapAnswer(
                gap_idx=0,
                answer_text="X is a thing because of Z.",
                source_kind="ai_general",
                answerer_model="claude-opus-4.7",
                verifier_model="gemini-2.5-pro",
            )
        ]
        out = apply_answers_to_gaps(body, ["q1", "q2", "q3"], ans)
        assert (
            "(AI synthesis — answered by claude-opus-4.7, verified by "
            "gemini-2.5-pro — verify before classroom use)" in out
        )

    def test_disclaimer_reflects_configured_model_labels(self) -> None:
        body = _gaps_body()
        ans = [
            GapAnswer(
                gap_idx=0,
                answer_text="X.",
                source_kind="ai_article",
                answerer_model="sonnet-4.6",
                verifier_model="gemini-flash",
            )
        ]
        out = apply_answers_to_gaps(body, ["q1", "q2", "q3"], ans)
        assert "answered by sonnet-4.6, verified by gemini-flash" in out

    def test_discussion_prompt_marks_in_place(self) -> None:
        body = _gaps_body()
        ans = [
            GapAnswer(
                gap_idx=1,
                answer_text="",
                source_kind="discussion",
            )
        ]
        out = apply_answers_to_gaps(body, ["q1", "q2", "q3"], ans)
        assert "*(open-ended discussion prompt — no factual answer)*" in out
        # Original question text must still be present
        assert "Why does this matter to you?" in out

    def test_no_source_format(self) -> None:
        body = _gaps_body()
        ans = [
            GapAnswer(
                gap_idx=2,
                answer_text="",
                source_kind="no_source",
            )
        ]
        out = apply_answers_to_gaps(body, ["q1", "q2", "q3"], ans)
        assert (
            "*(no authoritative source found — consider for class research "
            "project)*" in out
        )

    def test_verifier_rejection_with_issue(self) -> None:
        body = _gaps_body()
        ans = [
            GapAnswer(
                gap_idx=0,
                answer_text="",
                source_kind="verifier_rejected",
                rejection_reason="factual disagreement on mechanism",
            )
        ]
        out = apply_answers_to_gaps(body, ["q1", "q2", "q3"], ans)
        assert (
            "*(no defensible AI answer — verifier flagged: factual "
            "disagreement on mechanism)*" in out
        )

    def test_verifier_low_confidence(self) -> None:
        body = _gaps_body()
        ans = [
            GapAnswer(
                gap_idx=0,
                answer_text="",
                source_kind="verifier_low_confidence",
            )
        ]
        out = apply_answers_to_gaps(body, ["q1", "q2", "q3"], ans)
        assert "*(no answer — verifier confidence low on this claim)*" in out

    def test_verifier_rejection_does_not_leak_draft_text(self) -> None:
        body = _gaps_body()
        ans = [
            GapAnswer(
                gap_idx=0,
                answer_text="",
                source_kind="verifier_rejected",
                rejection_reason="conflates chromosome pairing with allele segregation",
            )
        ]
        out = apply_answers_to_gaps(body, ["q1", "q2", "q3"], ans)
        # The rejection_reason itself is rendered (it's the verifier's
        # short issue summary, not Opus's draft answer), but no actual
        # draft text from Opus should be in the output. The contract is
        # that the orchestrator passes answer_text="" for rejections; we
        # assert the answer paragraph format is the italic note, not Q/A.
        assert "**A:**" not in out  # rejection note isn't a full Q/A block
