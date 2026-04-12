"""Tests for raw/inbox/ staging helpers."""

from __future__ import annotations

from pathlib import Path

from app.agents.ingest.inbox import inbox_path, list_inbox, stage_to_inbox


class TestInbox:
    def test_inbox_path(self, tmp_path: Path) -> None:
        assert inbox_path(tmp_path / "knowledge") == tmp_path / "knowledge" / "raw" / "inbox"

    def test_stage_to_inbox_creates_file(self, tmp_path: Path) -> None:
        knowledge = tmp_path / "knowledge"
        content = b"fake html content"
        path = stage_to_inbox(knowledge, "test-source.html", content)
        assert path.exists()
        assert path.read_bytes() == content
        assert path.parent == inbox_path(knowledge)

    def test_stage_to_inbox_creates_dir(self, tmp_path: Path) -> None:
        knowledge = tmp_path / "knowledge"
        stage_to_inbox(knowledge, "test.html", b"content")
        assert inbox_path(knowledge).is_dir()

    def test_list_inbox_empty(self, tmp_path: Path) -> None:
        knowledge = tmp_path / "knowledge"
        assert list_inbox(knowledge) == []

    def test_list_inbox_returns_files(self, tmp_path: Path) -> None:
        knowledge = tmp_path / "knowledge"
        stage_to_inbox(knowledge, "a.html", b"a")
        stage_to_inbox(knowledge, "b.html", b"b")
        items = list_inbox(knowledge)
        assert len(items) == 2
        assert {p.name for p in items} == {"a.html", "b.html"}

    def test_list_inbox_excludes_sidecars(self, tmp_path: Path) -> None:
        knowledge = tmp_path / "knowledge"
        stage_to_inbox(knowledge, "a.html", b"a")
        stage_to_inbox(knowledge, "a.html.meta.json", b"{}")
        items = list_inbox(knowledge)
        assert len(items) == 1
        assert items[0].name == "a.html"
