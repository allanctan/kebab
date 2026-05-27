"""Tests for the batch verifier — models, adapter, and prompt builder."""

from __future__ import annotations

import pytest

from app.agents.research.batch_verifier import (
    BatchFinding,
    BatchVerifierDeps,
    BatchVerifyResult,
    _build_batch_prompt,
    batch_findings_to_tuples,
    batch_verify,
)
from app.agents.research.planner import ClaimEntry
from app.core.research.searcher import SourceContent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _claim(idx: int = 0) -> ClaimEntry:
    return ClaimEntry(
        text=f"Claim text {idx}",
        section="Introduction",
        paragraph=idx + 1,
    )


def _source(n: int = 1) -> SourceContent:
    return SourceContent(
        title=f"Source {n}",
        url=f"https://example.com/source-{n}",
        content=f"Content for source {n}. " * 20,
    )


def _confirm_finding(claim_idx: int = 0) -> BatchFinding:
    return BatchFinding(
        claim_idx=claim_idx,
        outcome="confirm",
        source_title="Source 1",
        source_url="https://example.com/source-1",
        evidence_quote="Content for source 1.",
        reasoning="Source agrees with claim.",
    )


# ---------------------------------------------------------------------------
# BatchFinding model tests
# ---------------------------------------------------------------------------


class TestBatchFindingModel:
    def test_confirm_finding_constructs(self) -> None:
        f = _confirm_finding()
        assert f.outcome == "confirm"
        assert f.claim_idx == 0
        assert f.new_sentence is None
        assert f.contradiction is None
        assert f.dispute_category is None

    def test_append_finding_constructs(self) -> None:
        f = BatchFinding(
            claim_idx=1,
            outcome="append",
            source_title="Wikipedia",
            source_url="https://en.wikipedia.org/wiki/Topic",
            evidence_quote="New fact from source.",
            new_sentence="New sentence added to the article.",
            reasoning="Source provides new related information.",
        )
        assert f.outcome == "append"
        assert f.new_sentence == "New sentence added to the article."
        assert f.contradiction is None

    def test_dispute_finding_constructs(self) -> None:
        f = BatchFinding(
            claim_idx=2,
            outcome="dispute",
            source_title="Wikipedia",
            source_url="https://en.wikipedia.org/wiki/Topic",
            evidence_quote="Source says the opposite.",
            contradiction="Source contradicts the claim directly.",
            dispute_category="factual_error",
            reasoning="Clear factual disagreement.",
        )
        assert f.outcome == "dispute"
        assert f.contradiction is not None
        assert f.dispute_category == "factual_error"

    def test_unverified_finding_constructs(self) -> None:
        f = BatchFinding(
            claim_idx=3,
            outcome="unverified",
            reasoning="No source addresses this claim.",
        )
        assert f.outcome == "unverified"
        assert f.source_title == ""
        assert f.source_url == ""
        assert f.evidence_quote == ""

    def test_outcome_normalized_to_lowercase(self) -> None:
        f = BatchFinding(
            claim_idx=0,
            outcome="CONFIRM",  # type: ignore[arg-type]
            source_title="S",
            source_url="https://example.com",
            evidence_quote="quote",
            reasoning="ok",
        )
        assert f.outcome == "confirm"

    def test_outcome_normalized_mixed_case(self) -> None:
        f = BatchFinding(
            claim_idx=0,
            outcome="Append",  # type: ignore[arg-type]
            source_title="S",
            source_url="https://example.com",
            evidence_quote="quote",
            reasoning="ok",
        )
        assert f.outcome == "append"

    def test_dispute_category_normalized_from_title_case(self) -> None:
        f = BatchFinding(
            claim_idx=0,
            outcome="dispute",
            source_title="S",
            source_url="https://example.com",
            evidence_quote="quote",
            dispute_category="Factual Error",  # type: ignore[arg-type]
            contradiction="Wrong.",
            reasoning="ok",
        )
        assert f.dispute_category == "factual_error"

    def test_dispute_category_normalized_from_screaming_snake(self) -> None:
        f = BatchFinding(
            claim_idx=0,
            outcome="dispute",
            source_title="S",
            source_url="https://example.com",
            evidence_quote="quote",
            dispute_category="MISLEADING_SIMPLIFICATION",  # type: ignore[arg-type]
            contradiction="Oversimplified.",
            reasoning="ok",
        )
        assert f.dispute_category == "misleading_simplification"

    def test_dispute_category_normalized_hyphens_to_underscores(self) -> None:
        f = BatchFinding(
            claim_idx=0,
            outcome="dispute",
            source_title="S",
            source_url="https://example.com",
            evidence_quote="quote",
            dispute_category="contested-or-opinion",  # type: ignore[arg-type]
            contradiction="Opinion.",
            reasoning="ok",
        )
        assert f.dispute_category == "contested_or_opinion"

    def test_invalid_outcome_rejected(self) -> None:
        with pytest.raises(Exception):
            BatchFinding(
                claim_idx=0,
                outcome="invented",  # type: ignore[arg-type]
                reasoning="bad",
            )

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(Exception):
            BatchFinding(
                claim_idx=0,
                outcome="confirm",
                source_title="S",
                source_url="https://example.com",
                evidence_quote="q",
                reasoning="ok",
                unexpected_field="nope",  # type: ignore[call-arg]
            )


# ---------------------------------------------------------------------------
# BatchVerifyResult model tests
# ---------------------------------------------------------------------------


class TestBatchVerifyResult:
    def test_wraps_findings_list(self) -> None:
        result = BatchVerifyResult(findings=[_confirm_finding(0), _confirm_finding(1)])
        assert len(result.findings) == 2
        assert result.findings[0].claim_idx == 0
        assert result.findings[1].claim_idx == 1

    def test_empty_findings_list_accepted(self) -> None:
        result = BatchVerifyResult(findings=[])
        assert result.findings == []


# ---------------------------------------------------------------------------
# batch_findings_to_tuples adapter tests
# ---------------------------------------------------------------------------


class TestBatchFindingsToTuples:
    def _claims(self, n: int = 3) -> list[ClaimEntry]:
        return [_claim(i) for i in range(n)]

    def test_converts_confirm_correctly(self) -> None:
        claims = self._claims(2)
        findings = [
            BatchFinding(
                claim_idx=0,
                outcome="confirm",
                source_title="Wiki",
                source_url="https://en.wikipedia.org/wiki/Topic",
                evidence_quote="The fact is confirmed.",
                reasoning="Source agrees.",
            )
        ]
        tuples = batch_findings_to_tuples(findings, claims)
        assert len(tuples) == 1
        claim, finding_result, title, url = tuples[0]
        assert claim is claims[0]
        assert finding_result.outcome == "confirm"
        assert title == "Wiki"
        assert url == "https://en.wikipedia.org/wiki/Topic"
        assert finding_result.evidence_quote == "The fact is confirmed."

    def test_skips_unverified(self) -> None:
        claims = self._claims(2)
        findings = [
            BatchFinding(
                claim_idx=0,
                outcome="unverified",
                reasoning="No source found.",
            ),
            _confirm_finding(1),
        ]
        tuples = batch_findings_to_tuples(findings, claims)
        assert len(tuples) == 1
        assert tuples[0][0] is claims[1]

    def test_suppresses_false_positive_dispute(self) -> None:
        claims = self._claims(2)
        findings = [
            BatchFinding(
                claim_idx=0,
                outcome="dispute",
                source_title="S",
                source_url="https://example.com",
                evidence_quote="Slightly different phrasing.",
                contradiction="Actually not a real contradiction.",
                dispute_category="false_positive",
                reasoning="Phrasing difference only.",
            )
        ]
        tuples = batch_findings_to_tuples(findings, claims)
        assert tuples == []

    def test_suppresses_acceptable_simplification_dispute(self) -> None:
        claims = self._claims(2)
        findings = [
            BatchFinding(
                claim_idx=0,
                outcome="dispute",
                source_title="S",
                source_url="https://example.com",
                evidence_quote="Technically more precise.",
                contradiction="Simplified for audience.",
                dispute_category="acceptable_simplification",
                reasoning="Grade-appropriate simplification.",
            )
        ]
        tuples = batch_findings_to_tuples(findings, claims)
        assert tuples == []

    def test_surfaces_factual_error_dispute(self) -> None:
        claims = self._claims(2)
        findings = [
            BatchFinding(
                claim_idx=1,
                outcome="dispute",
                source_title="S",
                source_url="https://example.com",
                evidence_quote="Evidence contradicts.",
                contradiction="Claim is factually wrong.",
                dispute_category="factual_error",
                reasoning="Source clearly contradicts.",
            )
        ]
        tuples = batch_findings_to_tuples(findings, claims)
        assert len(tuples) == 1
        _, finding_result, _, _ = tuples[0]
        assert finding_result.outcome == "dispute"
        assert finding_result.dispute_category == "factual_error"

    def test_surfaces_misleading_simplification_dispute(self) -> None:
        claims = self._claims(2)
        findings = [
            BatchFinding(
                claim_idx=0,
                outcome="dispute",
                source_title="S",
                source_url="https://example.com",
                evidence_quote="More nuanced picture.",
                contradiction="Misleads the reader.",
                dispute_category="misleading_simplification",
                reasoning="Oversimplified.",
            )
        ]
        tuples = batch_findings_to_tuples(findings, claims)
        assert len(tuples) == 1

    def test_surfaces_contested_or_opinion_dispute(self) -> None:
        claims = self._claims(1)
        findings = [
            BatchFinding(
                claim_idx=0,
                outcome="dispute",
                source_title="S",
                source_url="https://example.com",
                evidence_quote="Some experts disagree.",
                contradiction="Not universally agreed upon.",
                dispute_category="contested_or_opinion",
                reasoning="Contested topic.",
            )
        ]
        tuples = batch_findings_to_tuples(findings, claims)
        assert len(tuples) == 1

    def test_skips_out_of_range_claim_idx(self) -> None:
        claims = self._claims(2)  # indices 0 and 1 only
        findings = [
            BatchFinding(
                claim_idx=99,
                outcome="confirm",
                source_title="S",
                source_url="https://example.com",
                evidence_quote="quote",
                reasoning="ok",
            )
        ]
        tuples = batch_findings_to_tuples(findings, claims)
        assert tuples == []

    def test_skips_negative_claim_idx(self) -> None:
        claims = self._claims(2)
        findings = [
            BatchFinding(
                claim_idx=-1,
                outcome="confirm",
                source_title="S",
                source_url="https://example.com",
                evidence_quote="quote",
                reasoning="ok",
            )
        ]
        tuples = batch_findings_to_tuples(findings, claims)
        assert tuples == []

    def test_converts_append_finding(self) -> None:
        claims = self._claims(1)
        findings = [
            BatchFinding(
                claim_idx=0,
                outcome="append",
                source_title="Wiki",
                source_url="https://en.wikipedia.org/wiki/Topic",
                evidence_quote="New related fact.",
                new_sentence="This new sentence adds context.",
                reasoning="Relevant new info.",
            )
        ]
        tuples = batch_findings_to_tuples(findings, claims)
        assert len(tuples) == 1
        _, finding_result, _, _ = tuples[0]
        assert finding_result.outcome == "append"
        assert finding_result.new_sentence == "This new sentence adds context."

    def test_multiple_findings_mixed(self) -> None:
        claims = self._claims(4)
        findings = [
            _confirm_finding(0),
            BatchFinding(
                claim_idx=1,
                outcome="unverified",
                reasoning="No source.",
            ),
            BatchFinding(
                claim_idx=2,
                outcome="dispute",
                source_title="S",
                source_url="https://example.com",
                evidence_quote="q",
                contradiction="wrong",
                dispute_category="false_positive",
                reasoning="fp",
            ),
            BatchFinding(
                claim_idx=3,
                outcome="dispute",
                source_title="S",
                source_url="https://example.com",
                evidence_quote="evidence",
                contradiction="real contradiction",
                dispute_category="factual_error",
                reasoning="genuine error",
            ),
        ]
        tuples = batch_findings_to_tuples(findings, claims)
        # Only confirm(0) and factual_error dispute(3) should survive
        assert len(tuples) == 2
        assert tuples[0][0] is claims[0]
        assert tuples[1][0] is claims[3]


# ---------------------------------------------------------------------------
# _build_batch_prompt tests
# ---------------------------------------------------------------------------


class TestBuildBatchPrompt:
    def _make_deps(
        self,
        n_claims: int = 2,
        n_sources: int = 2,
        authoritative_domains: list[str] | None = None,
    ) -> BatchVerifierDeps:
        return BatchVerifierDeps(
            settings=None,  # type: ignore[arg-type]
            article_name="Plate Tectonics",
            article_body="# Plate Tectonics\n\nPlates move via convection.",
            claims=[_claim(i) for i in range(n_claims)],
            sources=[_source(i + 1) for i in range(n_sources)],
            authoritative_domains=authoritative_domains or [],
        )

    def test_includes_article_name(self) -> None:
        deps = self._make_deps()
        prompt = _build_batch_prompt(deps)
        assert "Plate Tectonics" in prompt

    def test_includes_article_body(self) -> None:
        deps = self._make_deps()
        prompt = _build_batch_prompt(deps)
        assert "Plates move via convection" in prompt

    def test_includes_all_claims_with_indices(self) -> None:
        deps = self._make_deps(n_claims=3)
        prompt = _build_batch_prompt(deps)
        assert '0. "Claim text 0"' in prompt
        assert '1. "Claim text 1"' in prompt
        assert '2. "Claim text 2"' in prompt

    def test_includes_claim_location(self) -> None:
        deps = self._make_deps(n_claims=1)
        prompt = _build_batch_prompt(deps)
        assert "section: Introduction" in prompt
        assert "paragraph: 1" in prompt

    def test_includes_source_title_and_url(self) -> None:
        deps = self._make_deps(n_sources=2)
        prompt = _build_batch_prompt(deps)
        assert "Source 1" in prompt
        assert "https://example.com/source-1" in prompt
        assert "Source 2" in prompt
        assert "https://example.com/source-2" in prompt

    def test_truncates_source_content_to_4000_chars(self) -> None:
        long_source = SourceContent(
            title="Long Source",
            url="https://example.com/long",
            content="x" * 8000,
        )
        deps = BatchVerifierDeps(
            settings=None,  # type: ignore[arg-type]
            article_name="Test",
            article_body="body",
            claims=[_claim(0)],
            sources=[long_source],
            authoritative_domains=[],
        )
        prompt = _build_batch_prompt(deps)
        # The truncated content is 4000 chars; the original 8000 should not appear
        assert "x" * 4001 not in prompt
        assert "x" * 4000 in prompt

    def test_includes_authoritative_domains(self) -> None:
        deps = self._make_deps(
            authoritative_domains=["example.org", "trusted.edu", "gov.ph"]
        )
        prompt = _build_batch_prompt(deps)
        assert "Authoritative Domains" in prompt
        assert "example.org" in prompt
        assert "trusted.edu" in prompt
        assert "gov.ph" in prompt

    def test_omits_authoritative_domains_when_empty(self) -> None:
        deps = self._make_deps(authoritative_domains=[])
        prompt = _build_batch_prompt(deps)
        assert "Authoritative Domains" not in prompt

    def test_limits_authoritative_domains_to_20(self) -> None:
        domains = [f"domain{i}.com" for i in range(30)]
        deps = self._make_deps(authoritative_domains=domains)
        prompt = _build_batch_prompt(deps)
        # domain19 is index 19 (the 20th), domain20 is index 20 (the 21st)
        assert "domain19.com" in prompt
        assert "domain20.com" not in prompt


# ---------------------------------------------------------------------------
# batch_verify integration with mocked agent
# ---------------------------------------------------------------------------


class TestBatchVerify:
    def _make_deps(self) -> BatchVerifierDeps:
        return BatchVerifierDeps(
            settings=None,  # type: ignore[arg-type]
            article_name="Test Article",
            article_body="# Test\n\nSome content.",
            claims=[_claim(0), _claim(1)],
            sources=[_source(1)],
            authoritative_domains=[],
        )

    def test_returns_findings_from_agent(self, mocker: pytest.fixture) -> None:
        expected_findings = [_confirm_finding(0), _confirm_finding(1)]
        mock_result = BatchVerifyResult(findings=expected_findings)

        mock_agent = mocker.MagicMock()
        mock_agent.run_sync.return_value.output = mock_result

        deps = self._make_deps()
        findings = batch_verify(None, deps, agent=mock_agent)  # type: ignore[arg-type]

        assert findings == expected_findings
        mock_agent.run_sync.assert_called_once()

    def test_passes_built_prompt_to_agent(self, mocker: pytest.fixture) -> None:
        captured: list[str] = []
        mock_result = BatchVerifyResult(findings=[_confirm_finding(0)])

        def fake_run_sync(prompt: str, *, deps: BatchVerifierDeps) -> object:
            captured.append(prompt)

            class _FakeResult:
                output = mock_result

            return _FakeResult()

        mock_agent = mocker.MagicMock()
        mock_agent.run_sync.side_effect = fake_run_sync

        deps = self._make_deps()
        batch_verify(None, deps, agent=mock_agent)  # type: ignore[arg-type]

        assert len(captured) == 1
        assert "Test Article" in captured[0]
        assert "Some content" in captured[0]
