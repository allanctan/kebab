"""Apply gap answers to a curated article body via AST manipulation.

Finds the ``## Research Gaps`` section in the AST, locates each answered
gap by its list-item index (not by text matching), and replaces it with
a Q/A block. Fixes bug #3 from the code review — position-based instead
of the fragile ``body.replace(old_line, ...)``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import marko.block

from app.core.markdown import _node_text, parse_body, render_body

logger = logging.getLogger(__name__)


@dataclass
class GapAnswer:
    """One answered gap, ready to be written into the body."""

    gap_idx: int
    answer_text: str
    source_title: str
    source_url: str


def _find_gaps_list_items(tree: marko.block.Document) -> list[tuple[int, int]]:
    """Return ``(parent_index, item_index)`` for each list item in ``## Research Gaps``.

    Walks the tree to find the Research Gaps heading (level 2), then
    collects all ``ListItem`` children from the first ``List`` node in
    that section. Returns an empty list if the section or list doesn't
    exist.
    """
    children = tree.children
    in_section = False
    items: list[tuple[int, int]] = []
    for i, node in enumerate(children):
        if isinstance(node, marko.block.Heading) and node.level == 2:
            text = _node_text(node).strip().lower()
            if text == "research gaps":
                in_section = True
                continue
            elif in_section:
                break
        if in_section and isinstance(node, marko.block.List):
            for j, item in enumerate(node.children):
                if isinstance(item, marko.block.ListItem):
                    items.append((i, j))
            break  # Only the first list in the section
    return items


def apply_answers_to_gaps(
    body: str,
    gaps: list[str],
    answers: list[GapAnswer],
) -> str:
    """Rewrite answered gap list-items as Q/A blocks.

    Finds each gap by its list-item index in the ``## Research Gaps``
    section (AST-based, not text-matching). Unanswered gaps are left
    untouched.
    """
    if not answers:
        return body

    tree = parse_body(body)
    list_items = _find_gaps_list_items(tree)

    for answer in answers:
        if answer.gap_idx < 0 or answer.gap_idx >= len(gaps):
            continue
        if answer.gap_idx >= len(list_items):
            logger.debug(
                "gaps writer: gap_idx %d out of range (%d items) — skipping",
                answer.gap_idx,
                len(list_items),
            )
            continue

        question = gaps[answer.gap_idx]
        clean = re.sub(r"\[\^\d+\]", "", answer.answer_text).strip()
        answered_md = (
            f"**Q: {question}**\n"
            f"  **A:** {clean} (Source: [{answer.source_title}]({answer.source_url}))"
        )

        parent_idx, item_idx = list_items[answer.gap_idx]
        list_node = tree.children[parent_idx]
        # Re-parse the answered markdown as list-item content
        snippet = parse_body(f"- {answered_md}\n")
        # The snippet should contain a List with one ListItem
        for snode in snippet.children:
            if isinstance(snode, marko.block.List) and snode.children:
                new_item = snode.children[0]
                list_node.children[item_idx] = new_item  # type: ignore[index]
                break

    return render_body(tree)


__all__ = ["GapAnswer", "apply_answers_to_gaps"]
