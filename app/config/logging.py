"""Logging setup for KEBAB.

Adapted from `better-ed-ai/app/config/logging.py`. Stripped of uvicorn-specific
handlers (KEBAB is CLI, not a server) but retains logfire instrumentation for
pydantic-ai and httpx observability — same tool, same dashboards as the sibling
project.

Logfire runs in local-only mode when ``LOGFIRE_TOKEN`` is unset, so development
works without any cloud credentials.
"""

import logging
import logging.config
from pathlib import Path

import logfire

from app.config.config import env

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
FILE_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

_logs_dir = Path(env.LOGS_DIR)
if not _logs_dir.is_absolute():
    _logs_dir = Path(__file__).parent.parent.parent / _logs_dir

LOGS_DIR = _logs_dir
LOGS_DIR.mkdir(parents=True, exist_ok=True)


LOGGING_CONFIG: dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": LOG_FORMAT,
            "datefmt": "%H:%M:%S",
        },
        "file": {
            "format": FILE_LOG_FORMAT,
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "level": "INFO",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOGS_DIR / "kebab.log"),
            "maxBytes": 10_485_760,  # 10 MB
            "backupCount": 5,
            "formatter": "file",
            "level": "DEBUG",
        },
        "logfire": {
            "class": "logfire.LogfireLoggingHandler",
            "level": "INFO",
        },
    },
    "root": {
        "handlers": ["console", "file", "logfire"],
        "level": "INFO",
    },
    "loggers": {
        "app": {"level": "DEBUG", "propagate": True},
    },
}


def setup_logging() -> None:
    """Configure logging globally. Safe to call multiple times.

    Logfire instrumentation:
    - ``pydantic_ai`` — traces every agent run, tool call, and model response.
    - ``httpx`` — captures outbound requests (web scraping, API calls).

    Runs in local-only mode when ``LOGFIRE_TOKEN`` is unset: no network egress,
    no dashboard, but instrumentation still works for local tracing.
    """
    from app.core.llm.trace import build_trace_processor

    trace_processor = build_trace_processor(LOGS_DIR)
    logfire.configure(
        token=env.LOGFIRE_TOKEN,
        environment=env.LOGFIRE_ENVIRONMENT,
        send_to_logfire="if-token-present",
        console=False,
        service_name="kebab",
        additional_span_processors=[trace_processor],
    )
    logfire.instrument_pydantic_ai()
    logfire.instrument_httpx(capture_all=True)

    logging.config.dictConfig(LOGGING_CONFIG)
