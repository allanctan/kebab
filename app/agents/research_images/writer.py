"""Append Wikipedia image markdown refs to a curated article body.

Pure markdown surgery: takes the approved :class:`ImageCandidate` list
and appends each as ``![description](relative/path)`` at the end of the
body. Filenames are derived from the candidate's local_path; descriptions
come from ``llm_description``.

There's no shared figure-numbering space with the PDF generate stage's
``[FIGURE:N]`` markers — those get resolved to plain markdown before the
body is saved, so collisions are not possible. The vestigial ``fig_num``
counter from today's ``research/agent.py`` is dropped.
"""

from __future__ import annotations

from app.agents.research_images.fetcher import ImageCandidate


def append_figure_refs(
    body: str,
    candidates: list[ImageCandidate],
    *,
    article_slug: str,
) -> str:
    """Append ``![desc](figures/<article_slug>/<filename>)`` markdown to the body.

    The relative path is computed from the candidate's local filename and
    the article slug — matching how generate-stage figures are referenced.
    The body is right-stripped before appending so there's exactly one
    blank line before the new figure block.
    """
    if not candidates:
        return body

    lines: list[str] = []
    for c in candidates:
        filename = c.local_path.name
        desc = (c.llm_description or c.raw_description or filename)[:150]
        rel = f"figures/{article_slug}/{filename}"
        lines.append(f"\n![{desc}]({rel})")

    return body.rstrip() + "\n" + "\n".join(lines) + "\n"


__all__ = ["append_figure_refs"]
