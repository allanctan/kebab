"""Apply gap answers to a curated article body.

Rewrites lines in the ``## Research Gaps`` section in-place: each
``- {question}`` line whose index appears in the ``answers`` list becomes
a Q/A bullet block citing the source.

Lifted from the gap-answering branch in ``research/agent.py`` (Step 3c)
during the 2026-04-12 research restructure. Today's `body.replace` shape
is preserved verbatim — fragility fixes are deferred to a follow-up spec.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class GapAnswer:
    """One answered gap, ready to be written into the body."""

    gap_idx: int
    answer_text: str
    source_title: str
    source_url: str


def apply_answers_to_gaps(
    body: str,
    gaps: list[str],
    answers: list[GapAnswer],
) -> str:
    """Rewrite ``- {question}`` lines as Q/A blocks for each answered gap.

    The ``gaps`` list is the original ordered list of gap questions
    extracted from the body (so ``gaps[answer.gap_idx]`` returns the
    matching question text). Lines whose gap_idx isn't answered are left
    untouched.
    """
    for answer in answers:
        if answer.gap_idx < 0 or answer.gap_idx >= len(gaps):
            continue
        question = gaps[answer.gap_idx]
        # Strip stray footnote markers that may have leaked in from the
        # classifier prompt — same defensive cleanup as today's code.
        clean = re.sub(r"\[\^\d+\]", "", answer.answer_text).strip()
        old_line = f"- {question}"
        answered = (
            f"- **Q: {question}**\n"
            f"  **A:** {clean} (Source: [{answer.source_title}]({answer.source_url}))"
        )
        body = body.replace(old_line, answered, 1)
    return body


__all__ = ["GapAnswer", "apply_answers_to_gaps"]
