"""Coverage builder — walk curated/ and produce a reverse LC index.

Reads every article under ``curated/``, pulls
``competency_codes: list[str]`` out of its frontmatter, and builds a
:class:`~app.models.curriculum.CompetencyCoverage` mapping LC code →
article IDs. Writes to
``knowledge/.kebab/curriculum/<name>.coverage.json``.

No LLM, no network. Pure I/O.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date
from pathlib import Path

from app.agents.curriculum.curriculum import load_spine
from app.config.config import Settings
from app.core.markdown import read_article
from app.models.curriculum import (
    CompetencyCoverage,
    CurriculumSpine,
    SubjectCoverage,
)

logger = logging.getLogger(__name__)


def _article_codes(curated_dir: Path) -> dict[str, list[str]]:
    """Walk ``curated_dir`` and collect ``article_id → competency_codes``."""
    result: dict[str, list[str]] = {}
    if not curated_dir.exists():
        return result
    for path in sorted(curated_dir.rglob("*.md")):
        try:
            fm, _, _ = read_article(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("coverage: skipping unparseable %s — %s", path, exc)
            continue
        data = fm.model_dump()
        codes = data.get("competency_codes") or []
        if isinstance(codes, list):
            result[fm.id] = [str(c) for c in codes if c]
    return result


def build_coverage(
    settings: Settings,
    *,
    name: str,
) -> CompetencyCoverage:
    """Build (and write) the coverage index for ``name``.

    Side effects:
        Writes ``knowledge/.kebab/curriculum/<name>.coverage.json``.

    Raises:
        FileNotFoundError: if the spine YAML for ``name`` doesn't exist.
    """
    spine: CurriculumSpine = load_spine(settings, name)

    # Reverse map: LC code → [article IDs]
    reverse: dict[str, list[str]] = defaultdict(list)
    by_id = _article_codes(Path(settings.CURATED_DIR))
    for article_id, codes in by_id.items():
        for code in codes:
            reverse[code].append(article_id)

    # Initialize every spine LC as an empty list so consumers can iterate
    # the full curriculum and see "no articles" cleanly. Codes outside the
    # spine are silently ignored — they belong to a sibling spine.
    competencies: dict[str, list[str]] = {c.code: [] for c in spine.competencies}
    for code in competencies:
        if code in reverse:
            competencies[code] = sorted(set(reverse[code]))

    # Per-subject roll-up
    by_subject: dict[str, SubjectCoverage] = {}
    for comp in spine.competencies:
        subj = comp.subject or "(unknown)"
        bucket = by_subject.setdefault(
            subj,
            SubjectCoverage(total=0, covered=0, uncovered=0),
        )
        bucket.total += 1
        if competencies.get(comp.code):
            bucket.covered += 1
        else:
            bucket.uncovered += 1

    total = len(spine.competencies)
    covered = sum(1 for code in competencies if competencies[code])

    coverage = CompetencyCoverage(
        name=name,
        generated_at=date.today(),
        total_competencies=total,
        covered=covered,
        uncovered=total - covered,
        by_subject=by_subject,
        competencies=competencies,
    )

    out_path = (
        Path(settings.KNOWLEDGE_DIR)
        / ".kebab"
        / "curriculum"
        / f"{name}.coverage.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(coverage.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    logger.info(
        "curriculum coverage: wrote %s — covered=%d/%d",
        out_path,
        covered,
        total,
    )
    return coverage


def load_coverage(settings: Settings, name: str) -> CompetencyCoverage:
    """Read a coverage JSON by name."""
    path = (
        Path(settings.KNOWLEDGE_DIR)
        / ".kebab"
        / "curriculum"
        / f"{name}.coverage.json"
    )
    if not path.exists():
        raise FileNotFoundError(f"coverage not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return CompetencyCoverage.model_validate(data)


__all__ = ["build_coverage", "load_coverage"]
