"""Apply research findings to a curated article body.

Takes a list of (claim, finding, source_title, source_url) tuples produced
by :mod:`app.agents.research.verifier` and rewrites the markdown body:

- ``confirm`` outcomes append a footnote citation to the claim's sentence.
- ``append`` outcomes insert a new sentence with footnote at the end of
  the claim's section.
- ``dispute`` outcomes append an entry to the ``## Disputes`` section.

Lifted from ``executor.py::apply_findings_to_article`` during the
2026-04-12 research restructure. No behavioral changes.
"""

from __future__ import annotations

import logging
import re

from app.agents.research.verifier import FindingTuple
from app.core.markdown import extract_section, next_footnote_number, parse_body
from app.core.markdown_ext import FootnoteDef

logger = logging.getLogger(__name__)

# Matches existing footnote defs: [^N]: [Title](URL) or [^N]: [id] [Title](URL)
_EXISTING_FOOTNOTE_RE = re.compile(r"^\[\^(\d+)\]:\s.*?\((https?://[^)]+)\)", re.MULTILINE)


def apply_findings_to_article(
    body: str,
    findings: list[FindingTuple],
) -> str:
    """Apply confirmed/appended/disputed findings to the article body.

    - confirm: add footnote citation to the claim's sentence
    - append: add new sentence with footnote after the relevant paragraph
    - dispute: add entry to ## Disputes section
    """
    tree = parse_body(body)
    footnote_num = next_footnote_number(tree)
    new_footnote_defs: list[str] = []
    # Pre-populate with URLs already in the body from prior runs (AST-based).
    url_to_footnote: dict[str, int] = {}
    for node in tree.children:
        if isinstance(node, FootnoteDef) and node.url.startswith("http"):
            url_to_footnote[node.url] = node.number
    disputes: list[str] = []
    appends: dict[str, list[str]] = {}  # section -> sentences to append

    def _get_footnote(source_title: str, source_url: str) -> str:
        nonlocal footnote_num
        if source_url in url_to_footnote:
            return f"[^{url_to_footnote[source_url]}]"
        num = footnote_num
        url_to_footnote[source_url] = num
        new_footnote_defs.append(
            f"[^{num}]: [{source_title}]({source_url})"
        )
        footnote_num += 1
        return f"[^{num}]"

    for claim, finding, source_title, source_url in findings:
        # Skip Research Gaps entirely — handled by research_gaps/writer.py
        if claim.section == "Research Gaps":
            continue

        if finding.outcome == "confirm":
            ref = _get_footnote(source_title, source_url)
            escaped = re.escape(claim.text)
            pattern = re.compile(f"({escaped})")
            if pattern.search(body):
                body = pattern.sub(rf"\1{ref}", body, count=1)

        elif finding.outcome == "append" and finding.new_sentence:
            ref = _get_footnote(source_title, source_url)
            sentence = f"{finding.new_sentence}{ref} <!-- appended -->"
            appends.setdefault(claim.section, []).append(sentence)

        elif finding.outcome == "dispute" and finding.contradiction:
            disputes.append(
                f"- **Claim**: \"{claim.text}\"\n"
                f"  **Section**: {claim.section}, paragraph {claim.paragraph}\n"
                f"  **External source**: [{source_title}]({source_url})\n"
                f"  **Contradiction**: {finding.contradiction}"
            )

    # Apply appends at end of their sections
    for section, sentences in appends.items():
        pattern_str = r"(^#{1,6}\s+" + re.escape(section) + r"\s*\n.*?)(?=^#{1,6}\s+|\Z)"
        section_pattern = re.compile(pattern_str, re.DOTALL | re.MULTILINE)
        match = section_pattern.search(body)
        if match:
            insert_text = "\n" + " ".join(sentences) + "\n"
            body = body[:match.end(1)] + insert_text + body[match.end(1):]

    # Ensure body ends cleanly before appending
    body = body.rstrip() + "\n"

    # Add disputes — append to existing section or create new one.
    # Dedup: skip disputes whose claim text is already in the section.
    if disputes:
        existing_disputes = extract_section(parse_body(body), "Disputes")
        fresh_disputes = [
            d for d in disputes
            if d.split("\n")[0] not in (existing_disputes or "")
        ]
        if fresh_disputes:
            if existing_disputes:
                # Append to existing section
                disputes_text = "\n\n".join(fresh_disputes)
                # Find end of disputes section
                pattern = re.compile(
                    r"(^##\s+Disputes\s*\n.*?)(?=^##\s+|\Z)",
                    re.DOTALL | re.MULTILINE,
                )
                match = pattern.search(body)
                if match:
                    body = body[:match.end(1)] + "\n\n" + disputes_text + "\n" + body[match.end(1):]
            else:
                body += "\n## Disputes\n\n" + "\n\n".join(fresh_disputes) + "\n"

    # Add new footnote definitions
    if new_footnote_defs:
        body += "\n" + "\n".join(new_footnote_defs) + "\n"

    return body


__all__ = ["apply_findings_to_article"]
