from __future__ import annotations

from app.agents.editorial.chief_editor import ClaimRewrite, UnresolvableDispute
from app.agents.editorial.writer import annotate_unresolvable, apply_rewrites


class TestApplyRewrites:
    def test_rewrites_claim_in_body(self) -> None:
        body = (
            "# Plate Tectonics\n\n"
            "Plates move at 10cm/year on average.\n\n"
            "## Disputes\n\n"
            "- **Claim**: \"Plates move at 10cm/year on average.\"\n"
            "  **Section**: Plate Tectonics, paragraph 1\n"
            "  **External source**: [Britannica](https://britannica.com/plates)\n"
            "  **Contradiction**: Source says 2-15cm/year.\n\n"
        )
        rewrite = ClaimRewrite(
            original_claim="Plates move at 10cm/year on average.",
            corrected_claim="Plates move at 2-15cm/year depending on the plate.",
            source_url="https://britannica.com/plates",
            reasoning="Britannica gives a range.",
        )
        new_body = apply_rewrites(body, [rewrite])
        assert "Plates move at 2-15cm/year depending on the plate." in new_body
        assert "Plates move at 10cm/year on average." not in new_body

    def test_no_rewrites_returns_body_unchanged(self) -> None:
        body = "# Test\n\nSome content.\n"
        assert apply_rewrites(body, []) == body

    def test_rewrite_not_found_logs_warning(self, caplog) -> None:
        body = "# Test\n\nSome content.\n"
        rewrite = ClaimRewrite(
            original_claim="Nonexistent claim text",
            corrected_claim="Replacement",
            source_url="https://example.com",
            reasoning="test",
        )
        result = apply_rewrites(body, [rewrite])
        assert result == body
        assert "not found in body" in caplog.text.lower()

    def test_multiple_rewrites_applied(self) -> None:
        body = (
            "Claim one is wrong. Claim two is also wrong.\n\n"
            "## Disputes\n\n"
            "- **Claim**: \"Claim one is wrong.\"\n"
            "  **Section**: Intro\n\n"
            "- **Claim**: \"Claim two is also wrong.\"\n"
            "  **Section**: Intro\n"
        )
        rewrites = [
            ClaimRewrite(
                original_claim="Claim one is wrong.",
                corrected_claim="Claim one is correct.",
                source_url="https://example.com/1",
                reasoning="test",
            ),
            ClaimRewrite(
                original_claim="Claim two is also wrong.",
                corrected_claim="Claim two is also correct.",
                source_url="https://example.com/2",
                reasoning="test",
            ),
        ]
        new_body = apply_rewrites(body, rewrites)
        assert "Claim one is correct." in new_body
        assert "Claim two is also correct." in new_body
        assert "Claim one is wrong." not in new_body
        assert "Claim two is also wrong." not in new_body


class TestAnnotateUnresolvable:
    def test_adds_unresolvable_marker(self) -> None:
        body = (
            "## Disputes\n\n"
            "- **Claim**: \"Convection is the primary driver.\"\n"
            "  **Section**: Forces\n"
        )
        dispute = UnresolvableDispute(
            claim="Convection is the primary driver.",
            reasoning="Contested among geologists.",
        )
        new_body = annotate_unresolvable(body, [dispute])
        assert "<!-- unresolvable -->" in new_body
        # Marker should appear before the claim
        marker_pos = new_body.index("<!-- unresolvable -->")
        claim_pos = new_body.index("**Claim**:")
        assert marker_pos < claim_pos

    def test_no_disputes_returns_body_unchanged(self) -> None:
        body = "## Disputes\n\nSome content.\n"
        assert annotate_unresolvable(body, []) == body

    def test_multiple_unresolvable_annotated(self) -> None:
        body = (
            "## Disputes\n\n"
            "- **Claim**: \"First disputed claim.\"\n"
            "  **Section**: Intro\n\n"
            "- **Claim**: \"Second disputed claim.\"\n"
            "  **Section**: Body\n"
        )
        disputes = [
            UnresolvableDispute(claim="First disputed claim.", reasoning="opinion"),
            UnresolvableDispute(claim="Second disputed claim.", reasoning="no source"),
        ]
        new_body = annotate_unresolvable(body, disputes)
        assert new_body.count("<!-- unresolvable -->") == 2
