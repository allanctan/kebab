"""Apply research findings to a curated article body via AST manipulation.

Takes a list of (claim, finding, source_title, source_url) tuples produced
by :mod:`app.agents.research.verifier` and modifies the markdown AST:

- ``confirm`` outcomes add a footnote reference after the claim's sentence.
- ``append`` outcomes insert a new paragraph at the end of the claim's section.
- ``dispute`` outcomes append entries to the ``## Disputes`` section.

Migrated from regex-based string surgery to marko AST in the 2026-04-12
markdown AST migration. Fixes:
- Bug #1: claim matching uses normalized whitespace instead of re.escape.
- Bug #2: footnote dedup uses FootnoteDef AST nodes instead of regex.
- Bug #4: section boundaries are walked via heading nodes, not regex.
"""

from __future__ import annotations

import logging
import re
from typing import Any, cast

import marko.block

from app.agents.research.verifier import FindingTuple
from app.core.markdown import (
    _node_text,
    extract_section,
    next_footnote_number,
    parse_body,
    render_body,
)
from app.core.markdown_ext import FootnoteDef

logger = logging.getLogger(__name__)


def _children(tree: marko.block.Document) -> list[Any]:
    """Return the document's children as a mutable list.

    marko types ``children`` as ``Sequence[Element]`` but the runtime value
    is always a plain ``list``. This cast satisfies basedpyright.
    """
    return cast(list[Any], tree.children)


def _parse_snippet(text: str) -> list[object]:
    """Parse a markdown snippet and return its block-level children."""
    doc = parse_body(text)
    return list(doc.children)


def _normalize_ws(s: str) -> str:
    """Collapse runs of whitespace into single spaces for fuzzy matching."""
    return re.sub(r"\s+", " ", s).strip()


def _find_paragraph_containing(
    tree: marko.block.Document, claim_text: str
) -> int | None:
    """Return the index of the first paragraph node containing claim_text.

    Uses normalized whitespace comparison to handle minor differences
    between the planner's extracted claim text and the body. Returns
    None if no paragraph contains the text.
    """
    normalized_claim = _normalize_ws(claim_text)
    for i, node in enumerate(_children(tree)):
        if isinstance(node, marko.block.Paragraph):
            para_text = _normalize_ws(_node_text(node))
            if normalized_claim in para_text:
                return i
    return None


def _insert_ref_in_paragraph(
    tree: marko.block.Document, para_idx: int, claim_text: str, ref: str
) -> None:
    """Insert a footnote ref after the claim text within a paragraph.

    Re-parses the paragraph after the string edit to keep the AST consistent.
    Uses a whitespace-tolerant regex derived from the claim words.
    """
    para = _children(tree)[para_idx]
    doc = marko.block.Document()
    doc.children = [para]  # type: ignore[assignment]
    para_md = render_body(doc)

    # Build a whitespace-tolerant pattern from the claim words
    words = _normalize_ws(claim_text).split()
    if not words:
        return
    pattern = r"\s+".join(re.escape(w) for w in words)
    match = re.search(pattern, para_md)
    if not match:
        return

    # Insert the ref right after the matched claim text
    pos = match.end()
    new_md = para_md[:pos] + ref + para_md[pos:]

    # Re-parse and replace the paragraph node
    new_nodes = _parse_snippet(new_md.strip())
    if new_nodes:
        _children(tree)[para_idx] = new_nodes[0]


def _find_section_end(tree: marko.block.Document, heading: str) -> int | None:
    """Return the index AFTER the last child of the named section.

    The section starts after the first heading node whose text matches
    ``heading`` (any level) and ends before the next heading at the same
    or higher level, or at the end of the document.
    """
    in_section = False
    section_level = 0
    section_end = None
    for i, node in enumerate(_children(tree)):
        if isinstance(node, marko.block.Heading):
            text = _node_text(node)
            if not in_section and text.strip().lower() == heading.strip().lower():
                in_section = True
                section_level = node.level
                section_end = i + 1
                continue
            elif in_section and node.level <= section_level:
                return section_end
        if in_section:
            section_end = i + 1
    return section_end if in_section else None


def apply_findings_to_article(
    body: str,
    findings: list[FindingTuple],
) -> str:
    """Apply confirmed/appended/disputed findings to the article body.

    - confirm: add footnote citation after the claim's sentence (AST paragraph walk)
    - append: add new sentence at the end of the claim's section (AST section walk)
    - dispute: add entry to ## Disputes section (AST section find/create)
    """
    tree = parse_body(body)
    footnote_num = next_footnote_number(tree)
    new_footnote_defs: list[FootnoteDef] = []

    # Pre-populate with URLs already in the body from prior runs (AST-based).
    url_to_footnote: dict[str, int] = {}
    for node in _children(tree):
        if isinstance(node, FootnoteDef) and node.url.startswith("http"):
            url_to_footnote[node.url] = node.number

    def _get_footnote(source_title: str, source_url: str) -> str:
        nonlocal footnote_num
        if source_url in url_to_footnote:
            return f"[^{url_to_footnote[source_url]}]"
        num = footnote_num
        url_to_footnote[source_url] = num
        new_footnote_defs.append(
            FootnoteDef.__new__(FootnoteDef)
        )
        fdef = new_footnote_defs[-1]
        fdef.number = num
        fdef.title = source_title
        fdef.url = source_url
        fdef.source_id = None
        fdef.children = []  # type: ignore[assignment]
        footnote_num += 1
        return f"[^{num}]"

    disputes: list[str] = []
    appends: dict[str, list[str]] = {}  # section -> sentences

    for claim, finding, source_title, source_url in findings:
        if claim.section == "Research Gaps":
            continue

        if finding.outcome == "confirm":
            ref = _get_footnote(source_title, source_url)
            para_idx = _find_paragraph_containing(tree, claim.text)
            if para_idx is not None:
                _insert_ref_in_paragraph(tree, para_idx, claim.text, ref)
            else:
                logger.debug(
                    "writer: claim %r not found in any paragraph — skipping confirm ref",
                    claim.text[:60],
                )

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

    # Apply appends at end of their sections (AST-based section boundaries)
    # Process in reverse order so insertions don't shift indices
    append_insertions: list[tuple[int, list[object]]] = []
    for section, sentences in appends.items():
        end_idx = _find_section_end(tree, section)
        if end_idx is not None:
            snippet_md = " ".join(sentences) + "\n"
            nodes = _parse_snippet(snippet_md)
            append_insertions.append((end_idx, nodes))

    children = _children(tree)
    for idx, nodes in sorted(append_insertions, key=lambda t: t[0], reverse=True):
        for j, node in enumerate(nodes):
            children.insert(idx + j, node)

    # Add disputes — find or create ## Disputes section
    if disputes:
        existing_disputes = extract_section(tree, "Disputes")
        fresh_disputes = [
            d for d in disputes
            if d.split("\n")[0] not in (existing_disputes or "")
        ]
        if fresh_disputes:
            disputes_md = "\n\n".join(fresh_disputes) + "\n"
            end_idx = _find_section_end(tree, "Disputes")
            children = _children(tree)
            if end_idx is not None:
                # Append to existing section
                nodes = _parse_snippet(disputes_md)
                for j, node in enumerate(nodes):
                    children.insert(end_idx + j, node)
            else:
                # Create new section
                section_md = "## Disputes\n\n" + disputes_md
                nodes = _parse_snippet(section_md)
                children.extend(nodes)

    # Add new FootnoteDef nodes at the end of the tree
    _children(tree).extend(new_footnote_defs)

    return render_body(tree)


__all__ = ["apply_findings_to_article"]
