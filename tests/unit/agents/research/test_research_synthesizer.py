"""Tests for app.agents.research.synthesizer."""

from __future__ import annotations

import pytest

from app.agents.research.planner import ClaimEntry
from app.agents.research.synthesizer import SynthesizedAppend, merge_appends
from app.agents.research.verifier import FindingResult, FindingTuple


def _claim(section: str = "Intro") -> ClaimEntry:
    return ClaimEntry(text="Test claim", section=section, paragraph=1)


def _append_finding(sentence: str) -> FindingResult:
    return FindingResult(
        outcome="append",
        reasoning="new info",
        evidence_quote="evidence",
        new_sentence=sentence,
    )


def _confirm_finding() -> FindingResult:
    return FindingResult(
        outcome="confirm",
        reasoning="agrees",
        evidence_quote="evidence",
    )


class TestMergeAppends:
    def test_single_append_passes_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sections with exactly 1 append are left untouched — no LLM call."""
        findings: list[FindingTuple] = [
            (_claim(), _append_finding("Fact A."), "Source A", "https://a.com"),
        ]
        result = merge_appends(None, findings, {"https://a.com": "[^2]"})  # type: ignore[arg-type]
        appends = [f for _, f, _, _ in result if f.outcome == "append"]
        assert len(appends) == 1
        assert appends[0].new_sentence == "Fact A."

    def test_confirms_and_disputes_pass_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-append findings are never touched by the synthesizer."""
        findings: list[FindingTuple] = [
            (_claim(), _confirm_finding(), "Source A", "https://a.com"),
            (_claim(), _append_finding("Fact A."), "Source B", "https://b.com"),
        ]
        result = merge_appends(None, findings, {"https://b.com": "[^2]"})  # type: ignore[arg-type]
        confirms = [f for _, f, _, _ in result if f.outcome == "confirm"]
        assert len(confirms) == 1

    def test_multiple_appends_same_section_are_merged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two appends for 'Intro' get merged into one via the LLM stub."""
        # Stub the LLM agent to return a fixed synthesis
        monkeypatch.setattr(
            "app.agents.research.synthesizer._synthesize_one_section",
            lambda _settings, _section, appends, **_kw: (
                f"Merged: {' + '.join(s for s, _, _ in appends)}"
            ),
        )

        findings: list[FindingTuple] = [
            (_claim("Intro"), _append_finding("Fact A."), "Src A", "https://a.com"),
            (_claim("Intro"), _append_finding("Fact B."), "Src B", "https://b.com"),
        ]
        refs = {"https://a.com": "[^2]", "https://b.com": "[^3]"}
        result = merge_appends(None, findings, refs)  # type: ignore[arg-type]

        appends = [f for _, f, _, _ in result if f.outcome == "append"]
        assert len(appends) == 1
        assert "Merged: Fact A. + Fact B." in appends[0].new_sentence

    def test_different_sections_are_merged_independently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Appends from different sections stay separate."""
        monkeypatch.setattr(
            "app.agents.research.synthesizer._synthesize_one_section",
            lambda _settings, section, appends, **_kw: f"Merged [{section}]",
        )

        findings: list[FindingTuple] = [
            (_claim("Intro"), _append_finding("A."), "Src A", "https://a.com"),
            (_claim("Intro"), _append_finding("B."), "Src B", "https://b.com"),
            (_claim("Methods"), _append_finding("C."), "Src C", "https://c.com"),
            (_claim("Methods"), _append_finding("D."), "Src D", "https://d.com"),
        ]
        refs = {
            "https://a.com": "[^2]", "https://b.com": "[^3]",
            "https://c.com": "[^4]", "https://d.com": "[^5]",
        }
        result = merge_appends(None, findings, refs)  # type: ignore[arg-type]

        appends = [(f.new_sentence, s) for _, f, _, s in result if f.outcome == "append"]
        sections_merged = {s for _, s in appends}
        # Two merged findings, one per section (anchored to first source URL)
        assert len(appends) == 2
        assert "https://a.com" in sections_merged or "https://c.com" in sections_merged

    def test_noop_confirms_created_for_extra_source_urls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-anchor sources get confirm entries so the writer creates their footnotes."""
        monkeypatch.setattr(
            "app.agents.research.synthesizer._synthesize_one_section",
            lambda _s, _sec, appends, **_kw: "Merged.",
        )

        findings: list[FindingTuple] = [
            (_claim("Intro"), _append_finding("A."), "Src A", "https://a.com"),
            (_claim("Intro"), _append_finding("B."), "Src B", "https://b.com"),
        ]
        refs = {"https://a.com": "[^2]", "https://b.com": "[^3]"}
        result = merge_appends(None, findings, refs)  # type: ignore[arg-type]

        confirms = [(f, url) for _, f, _, url in result if f.outcome == "confirm"]
        # One noop confirm for the second URL
        assert len(confirms) == 1
        assert confirms[0][1] == "https://b.com"
        assert "merged" in confirms[0][0].evidence_quote.lower()
