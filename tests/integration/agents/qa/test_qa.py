"""Gap discovery agent with stubbed proposer."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.qa import qa as qa_agent
from app.config.config import Settings
from app.core.markdown import extract_research_gaps, read_article


def _gap_proposer(_settings: Settings, deps: qa_agent.QaDeps) -> qa_agent.GapDiscoveryResult:
    return qa_agent.GapDiscoveryResult(
        gap_questions=[
            qa_agent.GapQuestion(
                question="What is the Ring of Fire?",
                reasoning="Article discusses plate boundaries but doesn't mention this.",
            ),
            qa_agent.GapQuestion(
                question="How fast do tectonic plates move?",
                reasoning="Article says plates move but gives no quantitative data.",
            ),
        ],
    )


def _empty_proposer(_settings: Settings, deps: qa_agent.QaDeps) -> qa_agent.GapDiscoveryResult:
    return qa_agent.GapDiscoveryResult(gap_questions=[])


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    knowledge = tmp_path / "knowledge"
    bio = knowledge / "curated" / "Science" / "Biology"
    bio.mkdir(parents=True)
    (bio / "photo.md").write_text(
        "---\n"
        "id: SCI-BIO-001\n"
        "name: Photosynthesis\n"
        "type: article\n"
        "sources:\n"
        "  - id: 0\n    title: OpenStax Biology 2e\n    tier: 2\n"
        "---\n\n"
        "# Photosynthesis\n\nLight reactions and Calvin cycle.\n",
        encoding="utf-8",
    )
    return Settings(
        KNOWLEDGE_DIR=knowledge,
        CURATED_DIR=knowledge / "curated",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )


@pytest.mark.integration
def test_qa_discovers_gaps(settings: Settings) -> None:
    result = qa_agent.run(settings, once=True, proposer=_gap_proposer)
    assert len(result.updated) == 1
    assert result.gaps_added == 2
    fm, body, tree = read_article(result.updated[0])
    gaps = extract_research_gaps(tree)
    assert len(gaps) == 2
    assert any("Ring of Fire" in g for g in gaps)


@pytest.mark.integration
def test_qa_skips_when_no_gaps(settings: Settings) -> None:
    result = qa_agent.run(settings, once=True, proposer=_empty_proposer)
    assert result.updated == []
    assert result.gaps_added == 0


@pytest.mark.integration
def test_qa_skips_articles_without_sources(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    bio = knowledge / "curated" / "Science" / "Biology"
    bio.mkdir(parents=True)
    (bio / "stub.md").write_text(
        "---\nid: X\nname: x\ntype: article\nsources: []\n---\n\nbody",
        encoding="utf-8",
    )
    settings = Settings(
        KNOWLEDGE_DIR=knowledge,
        CURATED_DIR=knowledge / "curated",
        QDRANT_PATH=None,
        QDRANT_URL=None,
        GOOGLE_API_KEY="test-key",
    )
    result = qa_agent.run(settings, once=True, proposer=_gap_proposer)
    assert result.updated == []
    assert any("no cited sources" in reason for _, reason in result.skipped)


@pytest.mark.integration
def test_qa_watch_mode_runs_n_iterations(settings: Settings) -> None:
    sleeps: list[float] = []
    counter = {"n": 0}

    def _fresh_gaps(_settings: Settings, deps: qa_agent.QaDeps) -> qa_agent.GapDiscoveryResult:
        counter["n"] += 1
        return qa_agent.GapDiscoveryResult(
            gap_questions=[
                qa_agent.GapQuestion(
                    question=f"Iteration gap {counter['n']}?",
                    reasoning="fresh",
                ),
            ],
        )

    def _fake_sleep(s: float) -> None:
        sleeps.append(s)

    result = qa_agent.run(
        settings,
        once=False,
        watch=True,
        proposer=_fresh_gaps,
        sleep_seconds=0.0,
        sleep_fn=_fake_sleep,
        iterations=3,
    )
    assert len(result.updated) == 3
    assert result.gaps_added == 3
    assert len(sleeps) == 2
