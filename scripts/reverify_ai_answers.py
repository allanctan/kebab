"""One-off re-verification of existing AI-synthesized gap answers.

Reads every Q/A block in curated articles whose trailing parenthetical
says ``(AI synthesis ...)`` and re-submits each one to the updated AI
verifier (which now has stricter precision/locale rules). If the verifier
no longer passes the gate (``verify_passes``), the Q/A block is replaced
with a verifier-rejected bullet — identical format to the writer's
``verifier_rejected`` rendering — and the frontmatter's ``gaps_answered``
counter is decremented accordingly.

External (web-sourced) Q/A blocks are not touched.

Safe by default. Pass ``--apply`` to actually rewrite articles.

Usage::

    uv run python scripts/reverify_ai_answers.py                      # dry-run, all curated
    uv run python scripts/reverify_ai_answers.py --apply              # apply, all curated
    uv run python scripts/reverify_ai_answers.py --subject Science    # dry-run, Science only
    uv run python scripts/reverify_ai_answers.py --apply --workers 8  # apply, 8 workers
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from app.agents.research_gaps.ai_verifier import (
    VerifierVerdict,
    verify_ai_answer,
    verify_passes,
)
from app.config.config import Settings
from app.core.audit import log_event, read_log
from app.core.markdown import read_article, write_article

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("reverify")

CURATED = Path("knowledge/curated")

QA_BLOCK_RE = re.compile(
    r"- \*\*Q:\s*(?P<question>.+?)\*\*\s*\n"
    r"\s*\*\*A:\*\*\s*(?P<answer_full>.+)",
    re.DOTALL,
)


@dataclass
class QABlock:
    """One AI-synthesized Q/A block in an article."""

    raw: str  # The full matched text incl. - **Q: ... **A: ... (AI synthesis ...)
    question: str
    answer_text: str  # Answer without the trailing parenthetical
    source_kind: str  # "ai_article" | "ai_general"


def _classify_ai_source(answer_line: str) -> str | None:
    """Return ai_article / ai_general / None (None = not an AI Q/A)."""
    if "(AI synthesis from article body" in answer_line:
        return "ai_article"
    if "(AI synthesis" in answer_line:
        return "ai_general"
    return None


def _split_answer(answer_line: str) -> str:
    """Strip the trailing ``(AI synthesis ...)`` parenthetical off the answer."""
    idx = answer_line.rfind("(AI synthesis")
    if idx == -1:
        return answer_line.strip()
    return answer_line[:idx].strip()


def extract_ai_qa_blocks(body: str) -> list[QABlock]:
    """Extract Q/A blocks whose answer line carries an ``(AI synthesis ...)`` tag.

    Returns one ``QABlock`` per match. The matched ``raw`` string covers
    everything from ``- **Q:`` through end of the answer line (single
    physical line for the A line).
    """
    blocks: list[QABlock] = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # Q line: "- **Q: ...**" — leading "-" or "- " then **Q:
        qm = re.match(r"-\s+\*\*Q:\s*(.+?)\*\*\s*$", line)
        if not qm:
            i += 1
            continue
        # A line is the next non-blank line, starting with **A:** (any indent).
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            i += 1
            continue
        a_line = lines[j]
        am = re.match(r"\s*\*\*A:\*\*\s*(.+)$", a_line)
        if not am:
            i += 1
            continue
        kind = _classify_ai_source(am.group(1))
        if kind is None:
            i = j + 1
            continue
        raw = "\n".join(lines[i : j + 1])
        answer_text = _split_answer(am.group(1))
        blocks.append(
            QABlock(
                raw=raw,
                question=qm.group(1).strip(),
                answer_text=answer_text,
                source_kind=kind,
            )
        )
        i = j + 1
    return blocks


def _rejection_bullet(question: str, verdict: VerifierVerdict) -> str:
    """Format identical to writer's verifier_rejected / verifier_low_confidence."""
    if verdict.verdict == "agree" and verdict.confidence == "low":
        note = "*(no answer — verifier confidence low on this claim)*"
    elif verdict.verdict == "partial":
        reason = (verdict.issue or "only partial coverage")[:60].rstrip(".")
        note = f"*(no answer — only partial coverage; verifier noted: {reason})*"
    else:
        reason = (verdict.issue or "verifier flagged the answer")[:60].rstrip(".")
        note = f"*(no defensible AI answer — verifier flagged: {reason})*"
    return f"- {question} {note}"


@dataclass
class ArticleResult:
    path: Path
    n_ai_blocks: int
    n_removed: int
    removed_details: list[tuple[QABlock, VerifierVerdict]]


def _already_reverified(path: Path) -> bool:
    """True if the audit log shows this article was already reverified."""
    try:
        for entry in read_log(path):
            if entry.get("stage") == "reverify":
                return True
    except Exception:
        return False
    return False


def process_article(
    settings: Settings,
    path: Path,
    *,
    dry_run: bool,
    log_lock: Lock,
) -> ArticleResult:
    """Re-verify every AI Q/A block in one article.

    Removed blocks are replaced with a rejection bullet in-place. Frontmatter
    ``gaps_answered`` is decremented by the number of removals.
    """
    if _already_reverified(path):
        return ArticleResult(path=path, n_ai_blocks=0, n_removed=0, removed_details=[])

    fm, body, _tree = read_article(path)
    blocks = extract_ai_qa_blocks(body)
    if not blocks:
        return ArticleResult(path=path, n_ai_blocks=0, n_removed=0, removed_details=[])

    summary = str(fm.model_dump().get("summary", "") or "")
    article_id = str(fm.model_dump().get("id", path.stem))

    removed: list[tuple[QABlock, VerifierVerdict]] = []
    for blk in blocks:
        try:
            verdict = verify_ai_answer(
                settings,
                question=blk.question,
                answer=blk.answer_text,
                article_summary=summary,
            )
        except Exception as exc:
            with log_lock:
                logger.warning(
                    "verifier call failed for %s :: %s — keeping answer",
                    path.name,
                    exc,
                )
            continue
        if not verify_passes(verdict):
            removed.append((blk, verdict))

    if not removed:
        if not dry_run:
            log_event(
                path,
                stage="reverify",
                action="article_reverified_no_removals",
                article_id=article_id,
                ai_blocks_checked=str(len(blocks)),
            )
        return ArticleResult(
            path=path,
            n_ai_blocks=len(blocks),
            n_removed=0,
            removed_details=[],
        )

    new_body = body
    for blk, verdict in removed:
        replacement = _rejection_bullet(blk.question, verdict)
        new_body = new_body.replace(blk.raw, replacement, 1)

    if not dry_run:
        prev = int(fm.model_dump().get("gaps_answered", 0) or 0)
        new_count = max(0, prev - len(removed))
        setattr(fm, "gaps_answered", new_count)
        write_article(path, fm, new_body)
        for blk, verdict in removed:
            log_event(
                path,
                stage="reverify",
                action="ai_answer_removed",
                article_id=article_id,
                question=blk.question,
                removed_answer=blk.answer_text,
                source_kind=blk.source_kind,
                verifier_verdict=verdict.verdict,
                verifier_confidence=verdict.confidence,
                verifier_issue=verdict.issue,
            )

    return ArticleResult(
        path=path,
        n_ai_blocks=len(blocks),
        n_removed=len(removed),
        removed_details=removed,
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="Actually rewrite. Default is dry-run.")
    p.add_argument("--subject", default=None, help="Filter to a subject (e.g. 'Science').")
    p.add_argument("--workers", type=int, default=8, help="Parallel article workers (default 8).")
    p.add_argument("--limit", type=int, default=None, help="Limit number of articles processed.")
    args = p.parse_args()

    settings = Settings()
    paths = sorted(CURATED.rglob("*.md"))
    if args.subject:
        paths = [p for p in paths if f"/{args.subject}/" in str(p)]
    if args.limit:
        paths = paths[: args.limit]

    if not paths:
        print("No articles to process.")
        return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] reverifying {len(paths)} articles with {args.workers} workers")
    print()

    log_lock = Lock()
    totals = {"articles": 0, "ai_blocks": 0, "removed": 0}
    article_results: list[ArticleResult] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_article, settings, path, dry_run=not args.apply, log_lock=log_lock): path
            for path in paths
        }
        done = 0
        for fut in as_completed(futures):
            done += 1
            try:
                r = fut.result()
            except Exception as exc:
                logger.exception("worker failed for %s", futures[fut])
                continue
            totals["articles"] += 1
            totals["ai_blocks"] += r.n_ai_blocks
            totals["removed"] += r.n_removed
            if r.n_removed:
                article_results.append(r)
            if done % 25 == 0 or done == len(paths):
                print(
                    f"  [{done}/{len(paths)}] articles_with_removals={len(article_results)} "
                    f"ai_blocks_seen={totals['ai_blocks']} removed={totals['removed']}"
                )

    print()
    print(f"[{mode}] DONE")
    print(f"  articles scanned:      {totals['articles']}")
    print(f"  AI Q/A blocks seen:    {totals['ai_blocks']}")
    print(f"  AI Q/A blocks removed: {totals['removed']}")
    if totals["ai_blocks"]:
        print(f"  removal rate:          {100*totals['removed']/totals['ai_blocks']:.1f}%")
    print()
    if article_results:
        print("Top 10 articles with most removals:")
        for r in sorted(article_results, key=lambda x: -x.n_removed)[:10]:
            print(f"  {r.n_removed}/{r.n_ai_blocks}  {r.path.relative_to(CURATED.parent)}")

    if not args.apply:
        print()
        print("Re-run with --apply to actually rewrite the articles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
