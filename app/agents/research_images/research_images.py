"""Research-images agent — enrich a curated article with Wikipedia figures.

Reads the article body, extracts Wikipedia targets from existing
footnotes (so requires :mod:`app.agents.research` to have run first),
fetches images for each target, prefilters by skip-keyword, downloads
them, asks the LLM to describe and reject decoratives, then appends the
approved images to the body.

Independent of :mod:`app.agents.research` and
:mod:`app.agents.research_gaps`. The supervisor agent (future) calls
this directly.
"""

from __future__ import annotations

import logging
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.agents.research_images.describer import describe
from app.agents.research_images.fetcher import (
    ImageCandidate,
    download,
    fetch_wikipedia_images,
    is_decorative_by_keyword,
    load_skip_keywords,
)
from app.agents.research_images.targets import extract_wikipedia_targets
from app.agents.research_images.writer import append_figure_refs
from app.config.config import Settings
from app.core.audit import log_event
from app.core.markdown import find_article_by_id, read_article, write_article

logger = logging.getLogger(__name__)


class ImagesResult(BaseModel):
    """Summary of one research-images run."""

    model_config = ConfigDict(extra="forbid")

    article_id: str = Field(..., description="ID of the article processed.")
    targets_found: int = Field(default=0, description="Wikipedia targets discovered in body.")
    images_added: int = Field(default=0, description="Images approved and appended.")
    decoratives_dropped: int = Field(default=0, description="Images dropped as decorative.")


def run(
    settings: Settings,
    *,
    article_id: str,
) -> ImagesResult:
    """Enrich an article with Wikipedia images.

    Args:
        settings:   KEBAB runtime configuration.
        article_id: ID of the article to enrich.

    Returns:
        :class:`ImagesResult` summarising the run.
    """
    path = find_article_by_id(settings.CURATED_DIR, article_id)
    if path is None:
        logger.warning("research-images: article %r not found — skipping", article_id)
        return ImagesResult(article_id=article_id)

    fm, body, _ = read_article(path)
    targets = extract_wikipedia_targets(body)
    if not targets:
        logger.info(
            "research-images: no Wikipedia footnotes in %r — run `kebab research` first",
            article_id,
        )
        return ImagesResult(article_id=article_id)

    article_slug = path.stem
    figures_dir = path.parent / "figures" / article_slug

    skip_keywords = load_skip_keywords(settings)
    candidates: list[ImageCandidate] = []
    seen_titles: set[str] = set()

    for target in targets:
        if target.title in seen_titles:
            continue
        seen_titles.add(target.title)

        images = fetch_wikipedia_images(target.title, limit=3)
        for img in images[:2]:
            if is_decorative_by_keyword(img, skip_keywords):
                log_event(
                    path, stage="research-images", action="image_dropped",
                    article_id=article_id,
                    reason="keyword_prefilter",
                    source_title=target.title,
                    raw_description=img.get("description", ""),
                )
                continue
            local_path = download(img, dest=figures_dir)
            if local_path is None:
                continue
            candidates.append(
                ImageCandidate(
                    local_path=local_path,
                    source_title=target.title,
                    raw_description=img.get("description", ""),
                )
            )

    approved: list[ImageCandidate] = []
    dropped = 0
    for c in candidates:
        desc = describe(settings, c)
        if desc == "DECORATIVE":
            c.local_path.unlink(missing_ok=True)
            dropped += 1
            log_event(
                path, stage="research-images", action="image_dropped",
                article_id=article_id,
                reason="decorative",
                source_title=c.source_title,
                filename=c.local_path.name,
                raw_description=c.raw_description,
            )
            continue
        approved.append(
            ImageCandidate(
                local_path=c.local_path,
                source_title=c.source_title,
                raw_description=c.raw_description,
                llm_description=desc,
            )
        )
        log_event(
            path, stage="research-images", action="image_added",
            article_id=article_id,
            description=desc,
            source_title=c.source_title,
            filename=c.local_path.name,
        )

    new_body = append_figure_refs(body, approved, article_slug=article_slug)

    setattr(fm, "images_added", len(approved))
    setattr(fm, "images_researched_at", date.today().isoformat())

    write_article(path, fm, new_body)
    logger.info(
        "research-images: wrote %r — targets=%d candidates=%d approved=%d dropped=%d",
        path.name,
        len(targets),
        len(candidates),
        len(approved),
        dropped,
    )

    return ImagesResult(
        article_id=article_id,
        targets_found=len(targets),
        images_added=len(approved),
        decoratives_dropped=dropped,
    )


__all__ = ["ImagesResult", "run"]
