"""Multimodal image description via Google Gemini.

Used by the PDF ingest stage to convert figures, diagrams, and charts
into grounded text. The description is inlined into the processed
text so downstream stages (organize, generate, verify) can reason
about the image content without ever touching binary data.

Uses ``google-genai`` directly — same pattern as :mod:`app.core.llm.embeddings`.
pydantic-ai's multimodal API adds indirection we don't need for a
string-in, string-out transform at a system boundary.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config.config import Settings
from app.core.errors import ConfigError, KebabError

logger = logging.getLogger(__name__)

#: Number of attempts for transient-error retry. First attempt + (N-1) retries.
_MAX_ATTEMPTS = 4
#: Base backoff in seconds. Doubles each retry: 1s, 2s, 4s, 8s.
_BACKOFF_BASE = 1.0
#: HTTP-ish status substrings that indicate a transient error worth retrying.
_TRANSIENT_MARKERS = ("503", "500", "502", "504", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")


_DESCRIBE_PROMPT = (
    "You are describing a figure extracted from an educational source "
    "(textbook, curriculum module). Write a 1–3 sentence caption that "
    "states what the figure shows in concrete terms — labels, axes, "
    "objects, relationships, quantities, directions. Do not speculate "
    "beyond what's visible. If the figure is purely decorative (logo, "
    "page border, blank space), return exactly: DECORATIVE."
)

#: Figures smaller than this area are skipped as decorative.
_MIN_AREA_PIXELS = 100 * 100


def _client(api_key: str) -> Any:
    """Build a genai client. Kept out of module scope so tests can patch."""
    if not api_key:
        raise ConfigError("KEBAB_GOOGLE_API_KEY is empty — required for multimodal")
    import google.genai as genai  # noqa: PLC0415 — namespace package

    return genai.Client(api_key=api_key)


def describe_image(
    image_bytes: bytes,
    mime_type: str,
    settings: Settings,
    *,
    width: int | None = None,
    height: int | None = None,
    context_hint: str | None = None,
) -> str:
    """Return a short grounded caption for ``image_bytes``.

    Returns the literal string ``"DECORATIVE"`` for images that should be
    dropped from the extracted text. Callers can use that as a sentinel
    to suppress rendering.
    """
    if width is not None and height is not None and width * height < _MIN_AREA_PIXELS:
        return "DECORATIVE"

    import google.genai as genai  # noqa: PLC0415
    from google.genai import types

    del genai  # keep the import next to the types import for clarity

    client = _client(settings.GOOGLE_API_KEY)
    # Resolve alias (e.g. "gemini-flash-lite") to actual model name.
    raw = settings.FIGURE_MODEL
    from app.core.llm.presets import get_entry
    entry = get_entry(raw)
    if entry is not None:
        model = entry.model
    elif ":" in raw:
        model = raw.split(":", 1)[1]
    else:
        model = raw

    # Gemini doesn't support SVG — convert to PNG via pymupdf.
    if mime_type == "image/svg+xml":
        import pymupdf
        doc = pymupdf.open(stream=image_bytes, filetype="svg")
        pix = doc[0].get_pixmap(dpi=150)
        image_bytes = pix.tobytes("png")
        mime_type = "image/png"

    prompt = _DESCRIBE_PROMPT
    if context_hint:
        prompt = f"{prompt}\n\nContext: {context_hint}"

    # Retry loop — Gemini flash-lite occasionally returns 503/429 under
    # load. Transient failures get exponential backoff up to _MAX_ATTEMPTS;
    # permanent failures (400/401/404/etc.) raise immediately.
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    prompt,
                ],
            )
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            msg = str(exc)
            is_transient = any(marker in msg for marker in _TRANSIENT_MARKERS)
            if not is_transient or attempt == _MAX_ATTEMPTS - 1:
                raise KebabError(f"Gemini multimodal call failed: {exc}") from exc
            backoff = _BACKOFF_BASE * (2 ** attempt)
            logger.info(
                "describe_image transient failure (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1,
                _MAX_ATTEMPTS,
                backoff,
                msg[:120],
            )
            time.sleep(backoff)
    else:  # pragma: no cover — defensive; the loop always breaks or raises
        raise KebabError(f"Gemini multimodal call failed after retries: {last_exc}")

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        return "DECORATIVE"
    # Normalize "DECORATIVE" / "decorative." / "DECORATIVE " variants to the canonical
    # sentinel. The describer prompt says to return exactly "DECORATIVE" but models
    # often append punctuation or lowercase, or emit both a description AND the
    # sentinel. Match loosely:
    #   1. Bare "DECORATIVE" (any case, any trailing punctuation)
    #   2. Prefix form: "DECORATIVE <reason>"
    #   3. Suffix form: "<description>. DECORATIVE." → the model signaled this is
    #      decorative after a throwaway description; honor the signal.
    normalized = text.strip().strip(".!").upper()
    if normalized == "DECORATIVE" or normalized.startswith("DECORATIVE "):
        return "DECORATIVE"
    # Suffix form: last token is literally "DECORATIVE" on its own line or after
    # a sentence break.
    tail = normalized.rsplit(". ", 1)[-1].strip() if ". " in normalized else ""
    if tail == "DECORATIVE":
        return "DECORATIVE"
    last_line = normalized.rsplit("\n", 1)[-1].strip()
    if last_line == "DECORATIVE":
        return "DECORATIVE"
    return text
