"""Q&A agent with stubbed proposer."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.qa import agent as qa_agent
from app.config.config import Settings
from app.core.markdown import extract_faq, read_article
from app.models.source import Source


def _src() -> Source:
    return Source(id=0, title="OpenStax Biology 2e", tier=2)


def _ready_proposer(_settings: Settings, deps: qa_agent.QaDeps) -> qa_agent.QaResult:
    return qa_agent.QaResult(
        reasoning="Two new questions cover the depth gaps.",
        new_questions=[
            qa_agent.QaPair(
                question="How do chloroplasts capture light?",
                answer="Pigments in the thylakoid membrane absorb photons.",
                sources=[_src()],
            ),
            qa_agent.QaPair(
                question="What gas does photosynthesis release?",
                answer="Oxygen, as a byproduct of water splitting.",
                sources=[_src()],
            ),
        ],
        is_ready_to_commit=True,
    )


def _not_ready_proposer(_settings: Settings, deps: qa_agent.QaDeps) -> qa_agent.QaResult:
    return qa_agent.QaResult(reasoning="not enough", new_questions=[], is_ready_to_commit=False)


def _duplicate_proposer(_settings: Settings, deps: qa_agent.QaDeps) -> qa_agent.QaResult:
    # Duplicates whatever already exists.
    pairs = [
        qa_agent.QaPair(question=q, answer="dup", sources=[_src()])
        for q in deps.existing_questions
    ]
    return qa_agent.QaResult(reasoning="dups", new_questions=pairs, is_ready_to_commit=True)


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
        "# Photosynthesis\n\nLight reactions and Calvin cycle.\n\n"
        "## Q&A\n\n**Q: What is photosynthesis?**\nA grounded answer.\n",
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
def test_qa_agent_appends_new_pairs(settings: Settings) -> None:
    result = qa_agent.run(settings, once=True, proposer=_ready_proposer)
    assert len(result.updated) == 1
    assert result.pairs_added == 2
    fm, body, _ = read_article(result.updated[0])
    assert len(extract_faq(body)) == 3  # 1 existing + 2 new


@pytest.mark.integration
def test_qa_agent_skips_when_not_ready(settings: Settings) -> None:
    result = qa_agent.run(settings, once=True, proposer=_not_ready_proposer)
    assert result.updated == []
    assert result.pairs_added == 0


@pytest.mark.integration
def test_qa_agent_skips_duplicates(settings: Settings) -> None:
    result = qa_agent.run(settings, once=True, proposer=_duplicate_proposer)
    assert result.updated == []
    assert result.pairs_added == 0


@pytest.mark.integration
def test_qa_agent_skips_articles_without_sources(tmp_path: Path) -> None:
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
    result = qa_agent.run(settings, once=True, proposer=_ready_proposer)
    assert result.updated == []
    assert any("no cited sources" in reason for _, reason in result.skipped)


@pytest.mark.integration
def test_qa_agent_watch_mode_runs_n_iterations(settings: Settings) -> None:
    sleeps: list[float] = []
    counter = {"n": 0}

    def _fresh_pairs(_settings: Settings, deps: qa_agent.QaDeps) -> qa_agent.QaResult:
        counter["n"] += 1
        return qa_agent.QaResult(
            reasoning="fresh",
            new_questions=[
                qa_agent.QaPair(
                    question=f"Iteration question {counter['n']}?",
                    answer="grounded",
                    sources=[_src()],
                ),
            ],
            is_ready_to_commit=True,
        )

    def _fake_sleep(s: float) -> None:
        sleeps.append(s)

    result = qa_agent.run(
        settings,
        once=False,
        watch=True,
        proposer=_fresh_pairs,
        sleep_seconds=0.0,
        sleep_fn=_fake_sleep,
        iterations=3,
    )
    assert len(result.updated) == 3
    assert result.pairs_added == 3
    assert len(sleeps) == 2  # sleeps between iterations only
