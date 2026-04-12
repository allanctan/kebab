"""Tests for app.core.audit — per-article audit logging."""

from __future__ import annotations

from pathlib import Path

from app.core.audit import log_event, read_log


class TestAuditLog:
    def test_log_event_creates_sidecar(self, tmp_path: Path) -> None:
        article = tmp_path / "article.md"
        article.write_text("body")

        log_event(article, stage="research", action="confirm", detail="Test claim confirmed")

        audit_file = tmp_path / "article.audit.jsonl"
        assert audit_file.exists()
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

    def test_log_event_handles_missing_parent_dir(self, tmp_path: Path) -> None:
        article = tmp_path / "deep" / "nested" / "article.md"
        # Parent dir doesn't exist — log_event should not crash
        log_event(article, stage="test", action="test", detail="test")
        # File may not exist (parent missing), but no exception raised
