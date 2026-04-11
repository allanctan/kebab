"""Built-in source adapters — thin wrappers around existing ingest code.

Each adapter satisfies :class:`app.core.source_adapter.SourceAdapter` and
delegates the actual file I/O to the legacy ingest functions
(``pdf.ingest``, ``csv_json.ingest``, ``web.ingest``) so the behavior
stays identical while the new protocol surface comes online.
"""
