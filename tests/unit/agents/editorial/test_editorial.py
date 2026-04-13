"""Unit tests for the editorial orchestrator.

All external calls are mocked — no I/O, no LLM calls, no network.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.agents.editorial.chief_editor import ClaimRewrite, UnresolvableDispute, Verdict
from app.agents.editorial.editorial import EditorialResult, run


def _make_verdict(
    decision: str = "accept",
    rewrites: list | None = None,
    unresolvable: list | None = None,
) -> Verdict:
    return Verdict(
        decision=decision,
        rewrites=rewrites or [],
        unresolvable=unresolvable or [],
        unconfirmed_claims=[],
        reasoning="test verdict",
    )


def _patch_common(mocker, *, article_path: Path = Path("/fake/art.md")) -> None:
    """Apply the baseline set of mocks used by every test."""
    mocker.patch(
        "app.agents.editorial.editorial.find_article_by_id",
        return_value=article_path,
    )
    mocker.patch(
        "app.agents.editorial.editorial._load_article_state",
        return_value=("body text", {}, "", "", []),
    )
    mocker.patch(
        "app.agents.editorial.editorial._write_final_frontmatter",
        return_value=None,
    )
    mocker.patch(
        "app.agents.editorial.editorial.auto_sync",
        return_value=None,
    )
    mocker.patch(
        "app.agents.editorial.editorial.log_event",
        return_value=None,
    )


@pytest.mark.unit
class TestEditorialOrchestrator:
    def test_single_cycle_accept(self, mocker, mock_env) -> None:
        """Chief editor accepts on cycle 1 — result has cycles=1, decision=accept."""
        _patch_common(mocker)
        mocker.patch(
            "app.agents.editorial.editorial._run_qa",
            return_value=MagicMock(gaps_added=2),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_research_gaps",
            return_value=MagicMock(answered=1, gaps_total=1),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_research",
            return_value=MagicMock(claims_total=5, confirms=4, disputes=0),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_chief_editor",
            return_value=_make_verdict("accept"),
        )

        result = run(mock_env, article_id="ART-001", max_cycles=3)

        assert isinstance(result, EditorialResult)
        assert result.cycles == 1
        assert result.decision == "accept"
        assert result.article_id == "ART-001"

    def test_loops_then_accepts(self, mocker, mock_env) -> None:
        """Chief editor loops once then accepts on cycle 2."""
        _patch_common(mocker)
        mocker.patch(
            "app.agents.editorial.editorial._run_qa",
            return_value=MagicMock(gaps_added=0),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_research_gaps",
            return_value=MagicMock(answered=0, gaps_total=0),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_research",
            return_value=MagicMock(claims_total=3, confirms=2, disputes=1),
        )
        verdicts = iter([_make_verdict("loop"), _make_verdict("accept")])
        mocker.patch(
            "app.agents.editorial.editorial._run_chief_editor",
            side_effect=verdicts,
        )

        result = run(mock_env, article_id="ART-002", max_cycles=3)

        assert result.cycles == 2
        assert result.decision == "accept"

    def test_stops_at_max_cycles(self, mocker, mock_env) -> None:
        """Chief editor always loops — decision becomes max_cycles_reached."""
        _patch_common(mocker)
        mocker.patch(
            "app.agents.editorial.editorial._run_qa",
            return_value=MagicMock(gaps_added=0),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_research_gaps",
            return_value=MagicMock(answered=0, gaps_total=0),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_research",
            return_value=MagicMock(claims_total=2, confirms=1, disputes=1),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_chief_editor",
            return_value=_make_verdict("loop"),
        )

        result = run(mock_env, article_id="ART-003", max_cycles=2)

        assert result.cycles == 2
        assert result.decision == "max_cycles_reached"

    def test_rewrites_applied(self, mocker, mock_env) -> None:
        """Verdict with rewrites triggers apply_rewrites and counts disputes_resolved."""
        rewrite = ClaimRewrite(
            original_claim="Plates move at 10cm/year",
            corrected_claim="Plates move at 2–15cm/year",
            source_url="https://britannica.com/tectonics",
            reasoning="Britannica gives a range.",
        )
        verdict = _make_verdict("accept", rewrites=[rewrite])

        _patch_common(mocker)
        mocker.patch(
            "app.agents.editorial.editorial._run_qa",
            return_value=MagicMock(gaps_added=0),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_research_gaps",
            return_value=MagicMock(answered=0, gaps_total=0),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_research",
            return_value=MagicMock(claims_total=1, confirms=0, disputes=1),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_chief_editor",
            return_value=verdict,
        )
        mock_apply = mocker.patch(
            "app.agents.editorial.editorial.apply_rewrites",
            return_value="updated body",
        )
        mock_annotate = mocker.patch(
            "app.agents.editorial.editorial.annotate_unresolvable",
            return_value="updated body",
        )
        fm_mock = MagicMock()
        mocker.patch(
            "app.agents.editorial.editorial.read_article",
            return_value=(fm_mock, "updated body", MagicMock()),
        )
        mock_write = mocker.patch(
            "app.agents.editorial.editorial.write_article",
            return_value=None,
        )

        result = run(mock_env, article_id="ART-004", max_cycles=3)

        assert result.disputes_resolved == 1
        assert result.decision == "accept"
        mock_apply.assert_called_once()
        mock_annotate.assert_not_called()
        mock_write.assert_called_once()

    def test_unresolvable_annotated(self, mocker, mock_env) -> None:
        """Verdict with unresolvable disputes triggers annotate_unresolvable and counts them."""
        dispute = UnresolvableDispute(
            claim="Convection is the primary driver",
            reasoning="Contested among geologists.",
        )
        verdict = _make_verdict("accept", unresolvable=[dispute])

        _patch_common(mocker)
        mocker.patch(
            "app.agents.editorial.editorial._run_qa",
            return_value=MagicMock(gaps_added=0),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_research_gaps",
            return_value=MagicMock(answered=0, gaps_total=0),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_research",
            return_value=MagicMock(claims_total=1, confirms=0, disputes=1),
        )
        mocker.patch(
            "app.agents.editorial.editorial._run_chief_editor",
            return_value=verdict,
        )
        mock_apply = mocker.patch(
            "app.agents.editorial.editorial.apply_rewrites",
            return_value="body",
        )
        mock_annotate = mocker.patch(
            "app.agents.editorial.editorial.annotate_unresolvable",
            return_value="annotated body",
        )
        fm_mock = MagicMock()
        mocker.patch(
            "app.agents.editorial.editorial.read_article",
            return_value=(fm_mock, "body", MagicMock()),
        )
        mock_write = mocker.patch(
            "app.agents.editorial.editorial.write_article",
            return_value=None,
        )

        result = run(mock_env, article_id="ART-005", max_cycles=3)

        assert result.disputes_unresolvable == 1
        assert result.decision == "accept"
        mock_annotate.assert_called_once()
        mock_apply.assert_not_called()
        mock_write.assert_called_once()
