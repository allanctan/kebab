"""Tests for app.core.audit — per-article audit logging."""

from __future__ import annotations

from pathlib import Path

import pytest

import app.core.audit as audit_module
from app.core.audit import log_event, read_log


@pytest.fixture(autouse=True)
def _use_tmp_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point audit logs to tmp_path/logs/ for test isolation."""
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(audit_module, "_logs_dir", logs)


class TestAuditLog:
    def test_log_event_creates_file(self, tmp_path: Path) -> None:
        article = tmp_path / "article.md"
        article.write_text("body")

        log_event(article, stage="research", action="confirm", detail="Test claim confirmed")

        entries = read_log(article)
        assert len(entries) == 1
        assert entries[0]["stage"] == "research"
        assert entries[0]["action"] == "confirm"
        assert entries[0]["detail"] == "Test claim confirmed"
        assert "ts" in entries[0]

    def test_log_event_appends(self, tmp_path: Path) -> None:
        article = tmp_path / "article.md"
        article.write_text("body")

        log_event(article, stage="research", action="confirm", detail="First")
        log_event(article, stage="research", action="append", detail="Second")

        entries = read_log(article)
        assert len(entries) == 2
        assert entries[0]["action"] == "confirm"
        assert entries[1]["action"] == "append"

    def test_log_event_includes_article_id(self, tmp_path: Path) -> None:
        article = tmp_path / "article.md"
        article.write_text("body")

        log_event(article, stage="research", action="confirm", detail="x", article_id="SCI-001")

        entries = read_log(article)
        assert entries[0]["article_id"] == "SCI-001"

    def test_read_log_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        article = tmp_path / "nonexistent.md"
        assert read_log(article) == []

    def test_file_lives_under_logs_dir(self, tmp_path: Path) -> None:
        article = tmp_path / "curated" / "Science" / "plate-tectonics.md"
        article.parent.mkdir(parents=True)
        article.write_text("body")

        log_event(article, stage="test", action="test", detail="test")

        logs = tmp_path / "logs"
        assert (logs / "plate-tectonics.audit.jsonl").exists()
