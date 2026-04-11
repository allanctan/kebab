"""Confidence-level computation.

Pure function over :class:`FrontmatterSchema` — no I/O, no side effects.
The confidence gate (>=3) is the production threshold consumers should
honor; healthcare requires 4 (human verified).

Updated for research-based verification: confidence is derived from
external source confirmation rate and dispute count rather than
multi-LLM same-source checks.
"""

from __future__ import annotations

from app.models.confidence import ConfidenceLevel
from app.models.frontmatter import FrontmatterSchema

_CONFIRM_THRESHOLD = 0.70


def compute_confidence(fm: FrontmatterSchema) -> ConfidenceLevel:
    """Return the confidence level implied by ``fm``.

    Rules:
        4 — ``human_verified is True``
        3 — research ran, >=70% claims confirmed, 0 disputes
        2 — research ran, <70% confirmed OR has disputes
        1 — >=1 source, not yet researched
        0 — no sources

    Legacy fallback for pre-research articles:
        3 — >=2 verifiers passed AND >=2 sources
        2 — >=1 verifier passed
    """
    if fm.human_verified:
        return 4

    extras = fm.model_dump()
    research_total = extras.get("research_claims_total")
    if research_total is not None and research_total > 0:
        confirms = extras.get("external_confirms", 0)
        disputes = extras.get("dispute_count", 0)
        ratio = confirms / research_total
        if disputes == 0 and ratio >= _CONFIRM_THRESHOLD:
            return 3
        return 2

    # Legacy fallback: check old-style verification records.
    passed = sum(1 for record in fm.verifications if record.passed)
    if passed >= 2 and len(fm.sources) >= 2:
        return 3
    if passed >= 1:
        return 2

    if len(fm.sources) >= 1:
        return 1
    return 0
