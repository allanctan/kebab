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
        body = _remove_dispute_entry(body, rewrite.original_claim, rewrite.source_url)

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


def _remove_dispute_entry(body: str, claim_text: str, source_url: str = "") -> str:
    """Remove a single dispute entry from the ## Disputes section.

    Tries two strategies:
    1. Match on claim text (first 60 chars) — works when the chief
       editor quotes the exact text from the dispute entry.
    2. Match on source URL — works when the chief editor paraphrases
       the claim but references the same source.

    Research writer formats disputes without a bullet prefix:
    ``**Claim**: "..."`` followed by ``**Category**:``, ``**Section**:``,
    etc. Each entry ends at the next ``**Claim**:``, ``* * *``, section
    heading, or end of string.
    """
    entry_end = r"(?=\n\*\*Claim\*\*:|\n\* \* \*|\n## |\Z)"

    # Strategy 1: match on claim text
    escaped_claim = re.escape(claim_text[:60])
    pattern = rf"\*\*Claim\*\*:[^\n]*{escaped_claim}.*?{entry_end}"
    match = re.search(pattern, body, flags=re.DOTALL)

    # Strategy 2: match on source URL if claim text didn't match
    if match is None and source_url:
        escaped_url = re.escape(source_url[:80])
        pattern = rf"\*\*Claim\*\*:.*?{escaped_url}.*?{entry_end}"
        match = re.search(pattern, body, flags=re.DOTALL)

    if match:
        body = body[: match.start()] + body[match.end() :]

    # Clean up orphaned separator lines left behind
    body = re.sub(r"\n\* \* \*\n+(?=## |\Z)", "\n", body)
    return body
