"""Domain-specific exceptions for KEBAB.

All errors raised by app code should inherit from :class:`KebabError`
so callers (CLI, tests) can catch a single base. Always preserve the
original cause via ``raise KebabError(...) from original``.
"""

from __future__ import annotations


class KebabError(Exception):
    """Base class for all KEBAB-specific errors."""


class ConfigError(KebabError):
    """Raised when settings are missing or invalid."""


class MarkdownError(KebabError):
    """Raised when a curated markdown file cannot be parsed or written."""


class IngestError(KebabError):
    """Raised when ingestion of a raw source fails."""


class SyncError(KebabError):
    """Raised when the sync stage cannot complete."""
