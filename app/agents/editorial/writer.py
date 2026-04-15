from __future__ import annotations

import logging
import re

from app.agents.editorial.chief_editor import ClaimRewrite, UnresolvableDispute

logger = logging.getLogger(__name__)


def apply_rewrites(body: str, rewrites: list[ClaimRewrite]) -> str:
    """Replace disputed claims in the body and remove their dispute entries.

    For each rewrite:
    1. Find and replace the original claim text with the corrected text.
    2. Remove the corresponding dispute entry from ## Disputes.
    """
    for rewrite in rewrites:
        if rewrite.original_claim not in body:
            logger.warning(
                "editorial writer: claim not found in body, skipping rewrite: %s",
                rewrite.original_claim[:80],
            )
            continue

        # Replace the claim text in the body
        body = body.replace(rewrite.original_claim, rewrite.corrected_claim, 1)

        # Remove the dispute entry that matches this claim
        body = _remove_dispute_entry(body, rewrite.original_claim)

    return body


def annotate_unresolvable(
    body: str, disputes: list[UnresolvableDispute]
) -> str:
    """Add <!-- unresolvable --> markers before dispute entries that can't be resolved."""
    for dispute in disputes:
        # Find the dispute entry line containing this claim
        escaped = re.escape(dispute.claim[:60])
        pattern = rf"(- \*\*Claim\*\*:.*?{escaped})"
        match = re.search(pattern, body, re.DOTALL)
        if match:
            # Insert the marker before the dispute entry
            body = body[: match.start()] + "<!-- unresolvable -->\n" + body[match.start() :]
    return body


def _remove_dispute_entry(body: str, claim_text: str) -> str:
    """Remove a single dispute entry from the ## Disputes section.

    Research writer formats disputes without a bullet prefix:
    ``**Claim**: "..."`` followed by ``**Category**:``, ``**Section**:``,
    etc., with a ``* * *`` separator between entries. This regex matches
    that format and stops at the next entry separator, the next
    ``**Claim**:``, a section heading, or end of string.
    """
    escaped = re.escape(claim_text[:60])
    pattern = (
        rf"\*\*Claim\*\*:[^\n]*{escaped}.*?"
        rf"(?=\n\*\*Claim\*\*:|\n\* \* \*|\n## |\Z)"
    )
    body = re.sub(pattern, "", body, count=1, flags=re.DOTALL)
    # Clean up orphaned separator lines left behind
    body = re.sub(r"\n\* \* \*\n+(?=## |\Z)", "\n", body)
    return body
