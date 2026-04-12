"""Stage 7: parse curated markdown → embed → upsert into Qdrant.

The sync stage is the bridge between the markdown source-of-truth and
the universal Qdrant index. It is **idempotent and resumable**: running
``kebab sync`` twice produces the same index, not duplicated points.

Pipeline:
    1. Walk ``settings.CURATED_DIR`` and collect ``.md`` files.
    2. For each: ``read_article`` → enforce token limit → ``extract_faq``
       → ``compute_confidence`` → build :class:`Article` payload.
    3. ``embed_batch`` the embed-text bundles in one call.
    4. For each touched ``(domain, subdomain, slug)`` group, delete-by-filter
       then upsert. Idempotency comes from the article ID being the
       deterministic Qdrant point ID (see :mod:`app.core.store`).
    5. Log a confidence histogram.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import marko.block

from app.config.config import Settings
from app.core.confidence import compute_confidence
from app.core.llm.embeddings import embed_batch
from app.core.errors import KebabError, SyncError
from app.core.markdown import extract_faq, read_article
from app.core.store import Store
from app.core.llm.tokens import count_tokens
from app.models.article import Article, LevelType
from app.models.context import ContextMapping
from app.models.frontmatter import FrontmatterSchema

logger = logging.getLogger(__name__)

#: Type of the embed function — overridable in tests.
EmbedBatch = Callable[[list[str], Settings], list[list[float]]]


@dataclass
class SyncResult:
    """Summary of a single sync run."""

    articles: int
    confidence_histogram: dict[int, int]
    skipped: list[tuple[Path, str]]


def _iter_markdown(root: Path) -> Iterator[Path]:
    """Yield every ``.md`` file under ``root``."""
    if not root.exists():
        return
    for path in sorted(root.rglob("*.md")):
        yield path


def _domain_from_path(path: Path, root: Path) -> tuple[str, str | None]:
    """Infer ``(domain, subdomain)`` from the markdown file's location."""
    parts = path.relative_to(root).parts
    if not parts:
        raise SyncError(f"unexpected markdown path layout: {path}")
    domain = parts[0]
    subdomain = parts[1] if len(parts) > 2 else None
    return domain, subdomain


def _embed_text(fm: FrontmatterSchema, body: str, faq: list[str]) -> str:
    """Build the text bundle that gets embedded for an article."""
    description = (
        getattr(fm, "description", None)
        or body[:500]
    )
    keywords = getattr(fm, "keywords", None) or []
    parts: list[str] = [fm.name, str(description), " ".join(keywords)]
    parts.extend(faq)
    return "\n\n".join(part for part in parts if part)


def _build_article(
    fm: FrontmatterSchema,
    body: str,
    tree: marko.block.Document,
    *,
    path: Path,
    domain: str,
    subdomain: str | None,
) -> Article:
    """Project frontmatter + body into the universal :class:`Article` payload."""
    faq = extract_faq(tree)
    extras = fm.model_dump()
    description = extras.get("description") or body.strip().splitlines()[0:1]
    description_text = (
        description if isinstance(description, str)
        else (description[0] if description else fm.name)
    )
    keywords = extras.get("keywords") or []
    contexts_raw = extras.get("contexts") or {}
    level_type_raw = (extras.get("level_type") or fm.type or "article").lower()
    if level_type_raw not in {"domain", "subdomain", "topic", "article"}:
        level_type_raw = "article"
    level_type: LevelType = level_type_raw  # type: ignore[assignment]
    parent_ids = extras.get("parent_ids") or []
    depth = extras.get("depth")
    if depth is None:
        depth = max(len(path.relative_to(path.parents[len(path.parents) - 1]).parts) - 1, 0)
    return Article(
        id=fm.id,
        name=fm.name,
        description=str(description_text),
        keywords=list(keywords),
        faq=faq,
        level_type=level_type,
        parent_ids=list(parent_ids),
        depth=int(depth),
        position=int(extras.get("position", 0)),
        domain=domain,
        subdomain=subdomain,
        prerequisites=list(fm.prerequisites),
        related=list(fm.related),
        md_path=str(path),
        confidence_level=compute_confidence(fm),
        contexts=ContextMapping.model_validate(contexts_raw),
    )


def run(
    settings: Settings,
    *,
    store: Store | None = None,
    embed_fn: EmbedBatch = embed_batch,
) -> SyncResult:
    """Execute the sync stage. Returns a :class:`SyncResult` summary."""
    root = Path(settings.CURATED_DIR)
    store = store or Store(settings)
    store.ensure_collection()

    articles: list[Article] = []
    embed_texts: list[str] = []
    touched_domains: set[str] = set()
    skipped: list[tuple[Path, str]] = []

    for path in _iter_markdown(root):
        try:
            fm, body, tree = read_article(path)
        except KebabError as exc:
            logger.warning("skip %s: %s", path, exc)
            skipped.append((path, str(exc)))
            continue
        token_count = count_tokens(body)
        if token_count > settings.MAX_TOKENS_PER_ARTICLE:
            msg = f"body exceeds {settings.MAX_TOKENS_PER_ARTICLE} tokens ({token_count})"
            logger.warning("skip %s: %s", path, msg)
            skipped.append((path, msg))
            continue
        domain, subdomain = _domain_from_path(path, root)
        article = _build_article(fm, body, tree, path=path, domain=domain, subdomain=subdomain)
        articles.append(article)
        embed_texts.append(_embed_text(fm, body, article.faq))
        touched_domains.add(domain)

    if not articles:
        logger.info("sync: no articles found under %s", root)
        return SyncResult(articles=0, confidence_histogram={}, skipped=skipped)

    vectors = embed_fn(embed_texts, settings)
    if len(vectors) != len(articles):
        raise SyncError(f"embed returned {len(vectors)} vectors for {len(articles)} articles")

    # Idempotency: clear all touched domains, then upsert. The deterministic
    # point ID would also handle re-runs, but the explicit delete catches
    # articles that have been *renamed* or *removed* from the markdown tree.
    for domain in touched_domains:
        store.delete_by_filter(Store.domain_filter(domain))
    store.upsert(list(zip(articles, vectors, strict=True)))

    histogram = Counter(article.confidence_level for article in articles)
    histogram_dict = {int(level): count for level, count in histogram.items()}
    logger.info(
        "sync: indexed %d articles across %d domain(s); confidence=%s",
        len(articles),
        len(touched_domains),
        dict(sorted(histogram_dict.items())),
    )
    return SyncResult(
        articles=len(articles),
        confidence_histogram=histogram_dict,
        skipped=skipped,
    )
