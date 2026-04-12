"""Describe a downloaded image via the LLM image describer.

Thin wrapper around :func:`app.core.images.image_describer.describe_image`.
The wrapper exists so the orchestrator can pass an :class:`ImageCandidate`
without knowing about MIME-type plumbing or context-hint construction.
"""

from __future__ import annotations

import logging

from app.agents.research_images.fetcher import ImageCandidate
from app.config.config import Settings
from app.core.images.image_describer import describe_image

logger = logging.getLogger(__name__)


def describe(settings: Settings, candidate: ImageCandidate) -> str:
    """Return the LLM caption for the candidate, or the literal ``"DECORATIVE"``.

    Errors during description are logged and swallowed — the caller treats
    "no description" as a soft failure and falls back to the candidate's
    raw Wikipedia description.
    """
    abs_path = candidate.local_path
    suffix = abs_path.suffix.lstrip(".")
    mime = "image/svg+xml" if suffix == "svg" else f"image/{suffix}"

    try:
        image_bytes = abs_path.read_bytes()
        return describe_image(
            image_bytes,
            mime,
            settings,
            context_hint=f"From Wikipedia article: {candidate.source_title}",
        )
    except Exception as exc:
        logger.debug(
            "research-images: describe failed for %s: %s", abs_path, exc
        )
        return candidate.raw_description[:200] or candidate.source_title


__all__ = ["describe"]
