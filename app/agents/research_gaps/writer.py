"""Apply gap answers to a curated article body via AST manipulation.

Finds the ``## Research Gaps`` section in the AST, locates each answered
gap by its list-item index (not by text matching), and rewrites it
according to ``GapAnswer.source_kind``:

- ``external``           → ``**Q:** … **A:** … (Source: [title](url))``
- ``ai_article``         → ``**Q:** … **A:** … (AI synthesis from article body
                            — answered by X, verified by Y — verify before
                            classroom use)``
- ``ai_general``         → ``**Q:** … **A:** … (AI synthesis — answered by X,
                            verified by Y — verify before classroom use)``
- ``discussion``         → bullet with italic ``*(open-ended discussion
                            prompt — no factual answer)*``
- ``no_source``          → bullet with italic ``*(no authoritative source
                            found — consider for class research project)*``
- ``verifier_rejected``  → bullet with italic ``*(no defensible AI answer
                            — verifier flagged: <reason>)*``
- ``verifier_low_confidence`` → bullet with italic ``*(no answer —
                            verifier confidence low on this claim)*``
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

import marko.block

from app.core.markdown import _node_text, parse_body, render_body

logger = logging.getLogger(__name__)

SourceKind = Literal[
    "external",
    "ai_article",
    "ai_general",
    "discussion",
    "no_source",
    "verifier_rejected",
    "verifier_low_confidence",
]


@dataclass
class GapAnswer:
    """One processed gap. ``source_kind`` controls how the writer renders it."""

    gap_idx: int
    answer_text: str
    source_kind: SourceKind = "external"
    # For source_kind == "external"
    source_title: str = ""
    source_url: str = ""
    # For source_kind in {"ai_article", "ai_general"}
    answerer_model: str = ""
    verifier_model: str = ""
    # For source_kind == "verifier_rejected"
    rejection_reason: str = ""


def _find_gaps_list_items(tree: marko.block.Document) -> list[tuple[int, int]]:
    """Return ``(parent_index, item_index)`` for each unanswered list item
    in ``## Research Gaps``. Skips already-answered Q/A blocks.
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
                if not isinstance(item, marko.block.ListItem):
                    continue
                item_text = _node_text(item).strip()
                if item_text.startswith("**Q:") or item_text.startswith("Q:"):
                    continue
                items.append((i, j))
            break
    return items


def _render_qa_block(question: str, answer: str, trailing: str) -> str:
    clean = re.sub(r"\[\^\d+\]", "", answer).strip()
    return f"**Q: {question}**\n  **A:** {clean} {trailing}".rstrip()


def _render_bullet_note(question: str, italic_note: str) -> str:
    return f"{question} {italic_note}"


def _build_replacement(question: str, ans: GapAnswer) -> str:
    """Return the markdown for the list item that replaces this gap."""
    if ans.source_kind == "external":
        trailing = f"(Source: [{ans.source_title}]({ans.source_url}))"
        return _render_qa_block(question, ans.answer_text, trailing)
    if ans.source_kind == "ai_article":
        trailing = (
            f"(AI synthesis from article body — answered by "
            f"{ans.answerer_model}, verified by {ans.verifier_model}"
            f" — verify before classroom use)"
        )
        return _render_qa_block(question, ans.answer_text, trailing)
    if ans.source_kind == "ai_general":
        trailing = (
            f"(AI synthesis — answered by {ans.answerer_model}, verified "
            f"by {ans.verifier_model} — verify before classroom use)"
        )
        return _render_qa_block(question, ans.answer_text, trailing)
    if ans.source_kind == "discussion":
        return _render_bullet_note(
            question, "*(open-ended discussion prompt — no factual answer)*"
        )
    if ans.source_kind == "no_source":
        return _render_bullet_note(
            question,
            "*(no authoritative source found — consider for class research project)*",
        )
    if ans.source_kind == "verifier_rejected":
        reason = ans.rejection_reason[:60].rstrip(".")
        return _render_bullet_note(
            question, f"*(no defensible AI answer — verifier flagged: {reason})*"
        )
    if ans.source_kind == "verifier_low_confidence":
        return _render_bullet_note(
            question, "*(no answer — verifier confidence low on this claim)*"
        )
    # Unknown kind — leave the bullet untouched
    return question


def apply_answers_to_gaps(
    body: str,
    gaps: list[str],
    answers: list[GapAnswer],
) -> str:
    """Rewrite gap list-items per ``GapAnswer.source_kind``."""
    if not answers:
        return body

    tree = parse_body(body)
    list_items = _find_gaps_list_items(tree)

    for ans in answers:
        if ans.gap_idx < 0 or ans.gap_idx >= len(gaps):
            continue
        if ans.gap_idx >= len(list_items):
            logger.debug(
                "gaps writer: gap_idx %d out of range (%d items) — skipping",
                ans.gap_idx,
                len(list_items),
            )
            continue

        parent_idx, item_idx = list_items[ans.gap_idx]
        list_node = tree.children[parent_idx]
        original_item = list_node.children[item_idx]  # type: ignore[index]
        original_text = _node_text(original_item).strip()
        # For Q/A block kinds use the gaps list question (which may differ
        # from original_text when the caller normalises question phrasing).
        # For bullet-note kinds preserve the exact original body text.
        if ans.source_kind in {"external", "ai_article", "ai_general"}:
            question = gaps[ans.gap_idx]
        else:
            question = original_text
        replacement_md = _build_replacement(question, ans)
        snippet = parse_body(f"- {replacement_md}\n")
        for snode in snippet.children:
            if isinstance(snode, marko.block.List) and snode.children:
                new_item = snode.children[0]
                list_node.children[item_idx] = new_item  # type: ignore[index]
                break

    return render_body(tree)


__all__ = ["GapAnswer", "SourceKind", "apply_answers_to_gaps"]
