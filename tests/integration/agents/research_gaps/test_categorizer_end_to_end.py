"""End-to-end routing test — one article exercises every category path."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

import app.core.audit as audit_module
from app.agents.research_gaps import research_gaps
from app.agents.research_gaps.ai_answerer import AIAnswer
from app.agents.research_gaps.ai_verifier import VerifierVerdict
from app.agents.research_gaps.categorizer import GapCategorization, GapCategory
from app.agents.research_gaps.classifier import GapClassification
from app.agents.research_gaps.query_planner import GapQuery, GapQueryPlan
from app.config.config import Settings
from app.core.audit import read_log
from app.core.research.searcher import SourceContent


ARTICLE_MD = """\
---
id: TEST-IT-001
name: Photosynthesis
section: Biology
domain: science
subdomain: biology
topic: photosynthesis
type: article
confidence_level: 1
parent_ids: []
source_files: []
summary: A short K-12 article on photosynthesis.
---

# Photosynthesis

Plants convert light energy into chemical energy stored in glucose.

## Research Gaps

- What is the magnitude of energy plants typically convert?
- What is the difference between aerobic and anaerobic respiration?
- How can a Grade 10 teacher explain glucose pathways simply?
- Why does this topic matter to you personally?
- How does the APECO project affect agricultural research in Aurora province?
"""


@pytest.mark.integration
def test_routing_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    curated = tmp_path / "curated"
    curated.mkdir()
    article_path = curated / "photosynthesis.md"
    article_path.write_text(ARTICLE_MD, encoding="utf-8")

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    monkeypatch.setattr(audit_module, "_logs_dir", logs_dir)

    settings = Settings(
        CURATED_DIR=curated,
        AI_ANSWERER_MODEL="opus-4.7",
        AI_VERIFIER_MODEL="gemini-pro",
        GAP_CATEGORIZER_MODEL="gemini-flash",
        LOGS_DIR=str(logs_dir),
    )

    # Categorize each gap differently in the batch call — cast to satisfy
    # basedpyright; pydantic's field_validator normalises the literal at runtime.
    _ordered_cats = [
        "factual",
        "conceptual",
        "pedagogical",
        "open_ended",
        "local_cultural",
    ]
    monkeypatch.setattr(
        research_gaps,
        "batch_categorize_gaps",
        lambda _s, questions, _summary: [
            GapCategorization(
                category=cast(GapCategory, _ordered_cats[i]), reasoning="stub"
            )
            for i in range(len(questions))
        ],
    )

    # Factual + local_cultural paths: search returns a source the classifier confirms.
    monkeypatch.setattr(
        research_gaps,
        "search",
        lambda _s, _adapter, _q, **kw: [
            SourceContent(
                title="Britannica: Photosynthesis",
                url="https://www.britannica.com/science/photosynthesis",
                content="Plants convert about 1-2% of solar energy into glucose.",
            )
        ],
    )
    monkeypatch.setattr(
        research_gaps,
        "answer_question",
        lambda _s, *, question, source_title, source_content: GapClassification(
            is_answered=True,
            answer="Plants convert about 1-2% of solar energy.",
            reasoning="Source directly answers.",
        ),
    )

    # Conceptual + pedagogical paths: AI answerer + verifier agree.
    monkeypatch.setattr(
        research_gaps,
        "ai_answer_gap",
        lambda _s, **kw: AIAnswer(
            answer="Aerobic uses oxygen; anaerobic does not.",
            grounded_in="general_knowledge",
            is_answered=True,
            reasoning="ok",
        ),
    )
    monkeypatch.setattr(
        research_gaps,
        "verify_ai_answer",
        lambda _s, **kw: VerifierVerdict(verdict="agree", confidence="high", issue=""),
    )

    # Query planner: produce one query per factual-routable gap (factual gap 0 + local_cultural gap 4).
    monkeypatch.setattr(
        research_gaps,
        "plan_queries",
        lambda _s, _d: GapQueryPlan(
            queries=[
                GapQuery(
                    query="energy plants convert", adapter="wikipedia", target_gap_idx=0
                ),
                GapQuery(query="APECO Aurora", adapter="wikipedia", target_gap_idx=4),
            ]
        ),
    )

    result = research_gaps.run(settings, article_id="TEST-IT-001", budget=50)
    body = article_path.read_text(encoding="utf-8")

    # 1) Each category renders the right format
    assert "(Source: [Britannica: Photosynthesis]" in body  # factual
    assert "AI synthesis" in body  # conceptual / pedagogical
    assert "*(open-ended discussion prompt" in body  # open_ended
    # local_cultural got the factual source (since search returned one)
    # — expect TWO external citations (factual + local_cultural)
    assert body.count("Britannica: Photosynthesis") >= 2

    # 2) Audit log has entries per gap
    log = read_log(article_path)
    actions = [e.get("action") for e in log]
    assert "gap_categorized" in actions
    assert "gap_answered" in actions  # factual + local_cultural
    assert "gap_ai_answered" in actions  # conceptual + pedagogical
    assert "gap_open_ended" in actions  # open_ended

    # 3) Result counts include external + AI-synth answers
    assert result.answered >= 4  # factual + conceptual + pedagogical + local_cultural


@pytest.mark.integration
def test_rerun_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Already-answered Q/A blocks are skipped on a second run."""
    curated = tmp_path / "curated"
    curated.mkdir()
    article_path = curated / "photosynthesis.md"
    article_path.write_text(ARTICLE_MD, encoding="utf-8")

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    monkeypatch.setattr(audit_module, "_logs_dir", logs_dir)

    settings = Settings(
        CURATED_DIR=curated,
        AI_ANSWERER_MODEL="opus-4.7",
        AI_VERIFIER_MODEL="gemini-pro",
        GAP_CATEGORIZER_MODEL="gemini-flash",
        LOGS_DIR=str(logs_dir),
    )

    # Categorize all as conceptual → AI path, verifier agrees, writes Q/A blocks.
    monkeypatch.setattr(
        research_gaps,
        "batch_categorize_gaps",
        lambda _s, questions, _summary: [
            GapCategorization(category="conceptual", reasoning="stub")
            for _ in questions
        ],
    )
    monkeypatch.setattr(
        research_gaps,
        "ai_answer_gap",
        lambda _s, **kw: AIAnswer(
            answer="An answer.",
            grounded_in="general_knowledge",
            is_answered=True,
            reasoning="ok",
        ),
    )
    monkeypatch.setattr(
        research_gaps,
        "verify_ai_answer",
        lambda _s, **kw: VerifierVerdict(verdict="agree", confidence="high", issue=""),
    )
    monkeypatch.setattr(
        research_gaps, "plan_queries", lambda *a, **kw: GapQueryPlan(queries=[])
    )
    monkeypatch.setattr(research_gaps, "search", lambda *a, **kw: [])

    research_gaps.run(settings, article_id="TEST-IT-001", budget=50)
    body_first = article_path.read_text(encoding="utf-8")
    ai_count_first = body_first.count("AI synthesis")
    assert ai_count_first > 0  # sanity: first run produced AI Q/A blocks

    research_gaps.run(settings, article_id="TEST-IT-001", budget=50)
    body_second = article_path.read_text(encoding="utf-8")
    ai_count_second = body_second.count("AI synthesis")

    # Already-answered Q/A blocks must be skipped → no new AI synthesis blocks added.
    assert ai_count_second == ai_count_first


@pytest.mark.integration
def test_rejected_answer_never_leaks_to_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifier-rejected draft answers must not appear in the article body."""
    curated = tmp_path / "curated"
    curated.mkdir()
    article_path = curated / "photosynthesis.md"
    article_path.write_text(ARTICLE_MD, encoding="utf-8")

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    monkeypatch.setattr(audit_module, "_logs_dir", logs_dir)

    settings = Settings(
        CURATED_DIR=curated,
        AI_ANSWERER_MODEL="opus-4.7",
        AI_VERIFIER_MODEL="gemini-pro",
        GAP_CATEGORIZER_MODEL="gemini-flash",
        LOGS_DIR=str(logs_dir),
    )

    SECRET_DRAFT = "Photosynthesis is powered by THE MOON not the SUN"
    monkeypatch.setattr(
        research_gaps,
        "batch_categorize_gaps",
        lambda _s, questions, _summary: [
            GapCategorization(category="conceptual", reasoning="def question")
            for _ in questions
        ],
    )
    monkeypatch.setattr(
        research_gaps,
        "ai_answer_gap",
        lambda _s, **kw: AIAnswer(
            answer=SECRET_DRAFT,
            grounded_in="general_knowledge",
            is_answered=True,
            reasoning="x",
        ),
    )
    monkeypatch.setattr(
        research_gaps,
        "verify_ai_answer",
        lambda _s, **kw: VerifierVerdict(
            verdict="disagree",
            confidence="high",
            issue="photosynthesis is powered by the sun, not the moon",
        ),
    )
    monkeypatch.setattr(
        research_gaps, "plan_queries", lambda *a, **kw: GapQueryPlan(queries=[])
    )
    monkeypatch.setattr(research_gaps, "search", lambda *a, **kw: [])

    research_gaps.run(settings, article_id="TEST-IT-001", budget=50)
    body = article_path.read_text(encoding="utf-8")
    audit = read_log(article_path)

    # The secret draft MUST be in the audit log...
    assert any(SECRET_DRAFT in str(e) for e in audit)
    # ...but MUST NOT appear in the article body.
    assert SECRET_DRAFT not in body
    # The italic rejection note MUST be in the body.
    assert "no defensible AI answer" in body
