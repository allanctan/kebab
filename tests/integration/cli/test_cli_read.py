"""Read-side CLI commands against an in-memory Qdrant store."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner
from qdrant_client import QdrantClient

from app.cli import main as cli
from app.config.config import Settings
from app.core.store import EMBEDDING_DIM, Store
from app.pipeline import sync as sync_stage

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "articles"


def _stub_embed(texts: list[str], _settings: Settings) -> list[list[float]]:
    return [[(i + 1) * 0.001] * EMBEDDING_DIM for i in range(len(texts))]


def _stub_query_embed(text: str, _settings: Settings) -> list[float]:
    return [0.001] * EMBEDDING_DIM


@pytest.fixture
def populated_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Settings, Store]:
    knowledge_dir = tmp_path / "knowledge"
    biology = knowledge_dir / "curated" / "Science" / "Biology"
    biology.mkdir(parents=True)
    shutil.copy(FIXTURES / "photosynthesis.md", biology / "photosynthesis.md")
    shutil.copy(FIXTURES / "cellular_respiration.md", biology / "cellular_respiration.md")

    settings = Settings(
        KNOWLEDGE_DIR=knowledge_dir,
        CURATED_DIR=knowledge_dir / "curated",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )
    store = Store(settings, client=QdrantClient(":memory:"))
    sync_stage.run(settings, store=store, embed_fn=_stub_embed)

    # Patch the CLI's module-level helpers to use our in-memory store + stub.
    import app.cli as cli_module

    monkeypatch.setattr(cli_module, "env", settings)
    monkeypatch.setattr(cli_module, "_store", lambda _settings: store)
    monkeypatch.setattr(cli_module, "embed", _stub_query_embed)
    return settings, store


@pytest.mark.integration
def test_status_lists_confidence_histogram(populated_index: tuple[Settings, Store]) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0, result.output
    assert "Total articles: 2" in result.output
    assert "confidence 1: 2" in result.output


@pytest.mark.integration
def test_search_prints_hits(populated_index: tuple[Settings, Store]) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["search", "photosynthesis"])
    assert result.exit_code == 0, result.output
    assert "SCI-BIO-001" in result.output or "SCI-BIO-002" in result.output


@pytest.mark.integration
def test_check_existing_article(populated_index: tuple[Settings, Store]) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["check", "SCI-BIO-001"])
    assert result.exit_code == 0, result.output
    assert "Photosynthesis" in result.output
    assert "confidence:" in result.output


@pytest.mark.integration
def test_check_missing_article_errors(populated_index: tuple[Settings, Store]) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["check", "NONEXISTENT"])
    assert result.exit_code != 0
    assert "no article" in result.output.lower()


@pytest.mark.integration
def test_tree_groups_by_subdomain(populated_index: tuple[Settings, Store]) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["tree", "Science"])
    assert result.exit_code == 0, result.output
    assert "Science / Biology" in result.output
    assert "SCI-BIO-001" in result.output
    assert "SCI-BIO-002" in result.output


@pytest.mark.integration
def test_tree_unknown_domain_is_empty(populated_index: tuple[Settings, Store]) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["tree", "Mathematics"])
    assert result.exit_code == 0
    assert "no articles" in result.output.lower()
