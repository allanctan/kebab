"""JSONL span exporter for LLM call tracing.

Plugs into Logfire's OpenTelemetry pipeline via ``additional_span_processors``.
Writes one JSON line per completed span to ``logs/llm-trace.jsonl``. Works
with or without a Logfire token — the local OTel SDK always runs.

Only captures spans from pydantic-ai agent runs (``agent.*`` or containing
``gen_ai``), not every HTTP request or internal span.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

logger = logging.getLogger(__name__)


class JsonlSpanExporter(SpanExporter):
    """Write completed spans as JSONL to a file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(path, "a", encoding="utf-8")  # noqa: SIM115

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:  # type: ignore[override]
        for span in spans:
            record = _span_to_dict(span)
            if record is not None:
                self._file.write(json.dumps(record, default=str) + "\n")
        self._file.flush()
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self._file.close()

    def force_flush(self, timeout_millis: int = 0) -> bool:
        self._file.flush()
        return True


def _span_to_dict(span: ReadableSpan) -> dict | None:
    """Convert a span to a JSON-serializable dict, or None if not an LLM span."""
    name = span.name or ""
    attrs = dict(span.attributes or {})

    # Filter: only capture pydantic-ai / gen_ai spans
    is_llm = (
        "gen_ai" in name.lower()
        or "agent" in name.lower()
        or any("gen_ai" in str(k) for k in attrs)
        or any("logfire.msg" in str(k) for k in attrs)
    )
    if not is_llm:
        return None

    start_ns = span.start_time or 0
    end_ns = span.end_time or 0
    duration_ms = (end_ns - start_ns) / 1_000_000 if end_ns > start_ns else 0

    record: dict = {
        "name": name,
        "duration_ms": round(duration_ms, 1),
    }

    # Extract useful attributes
    for key in sorted(attrs):
        val = attrs[key]
        k = str(key)
        if any(skip in k for skip in ("telemetry.", "otel.", "service.")):
            continue
        record[k] = val

    return record


def build_trace_processor(logs_dir: Path) -> SimpleSpanProcessor:
    """Return a span processor that writes LLM traces to JSONL.

    File is named ``llm-trace-YYYY-MM-DD.jsonl`` so each day gets its own file.
    """
    from datetime import date

    trace_path = logs_dir / f"llm-trace-{date.today().isoformat()}.jsonl"
    exporter = JsonlSpanExporter(trace_path)
    return SimpleSpanProcessor(exporter)
