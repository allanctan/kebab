"""Global pytest fixtures. Conventions mirror better-ed-ai/tests/conftest.py."""

import time
from pathlib import Path
from typing import Iterator

import pytest

from app.config import env


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    """Isolated knowledge/ tree for pipeline integration tests."""
    root = tmp_path / "knowledge"
    (root / "raw" / "documents").mkdir(parents=True)
    (root / "raw" / "datasets").mkdir(parents=True)
    return root


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch):
    """Override Settings for tests that must avoid real env vars."""
    monkeypatch.setattr(env, "LLM_CURATION_MODEL", "test:stub")
    monkeypatch.setattr(env, "EMBEDDING_MODEL", "test:stub")
    return env


class LatencyTracker:
    def __init__(self) -> None:
        self.start_time: float | None = None
        self.end_time: float | None = None
        self.duration: float | None = None

    def __enter__(self) -> "LatencyTracker":
        self.start_time = time.time()
        return self

    def __exit__(self, *args: object) -> None:
        self.end_time = time.time()
        if self.start_time is not None:
            self.duration = self.end_time - self.start_time
            print(f"\n[LATENCY] Operation took {self.duration:.3f}s")


@pytest.fixture
def track_latency() -> Iterator[LatencyTracker]:
    """Context manager for tracking operation latency in tests."""
    yield LatencyTracker()
