"""Direct-URL adapter — wraps :mod:`app.pipeline.ingest.web`.

``discover(query)`` treats ``query`` as a URL and returns a single
candidate. ``fetch(candidate)`` delegates to the legacy ``web.ingest()``
function, which already respects caching by writing raw HTML + text
under ``raw/documents/``. The adapter adds a provenance sidecar next
to the cached HTML with the full envelope (retrieved_at, checksum, ...).

This adapter does NOT route through :class:`SharedFetcher` in M17 — the
legacy ``web.ingest()`` uses :mod:`app.utils.web_scraper` directly. A
follow-up pass can migrate it through ``SharedFetcher`` once the
fetcher has been proven against the other M18–M20 channels.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from app.config.config import Settings
from app.core.sources.provenance import sha256_file, write_sidecar
from app.core.sources.adapter import (
    AdapterError,
    Candidate,
    FetchedArtifact,
    SourceTier,
)
from app.models.source import Source
from app.pipeline.ingest import web as web_ingest


@dataclass
class DirectUrlAdapter:
    """Direct-URL HTML fetcher. Default tier 4 (reputable platform, operator-chosen)."""

    settings: Settings
    name: ClassVar[str] = "direct_url"
    default_tier: SourceTier = field(default=4)

    def discover(self, query: str, *, limit: int = 10) -> list[Candidate]:
        """Treat ``query`` as a URL and return a one-element candidate list.

        This adapter has no real discovery surface — it's a convenience
        wrapper around a single fetch. Callers who want discovery
        should use a search adapter (Tavily, M18).
        """
        if not (query.startswith("http://") or query.startswith("https://")):
            return []
        return [
            Candidate(
                adapter=self.name,
                locator=query,
                title=query,
                snippet=None,
                tier_hint=self.default_tier,
            )
        ]

    def fetch(self, candidate: Candidate) -> FetchedArtifact:
        if candidate.adapter != self.name:
            raise AdapterError(
                f"direct_url cannot fetch candidate from adapter {candidate.adapter!r}"
            )
        result = web_ingest.ingest(self.settings, candidate.locator)
        raw_path = result.raw_path
        content_hash = sha256_file(raw_path)
        source = Source(
            id=0,
            title=candidate.title,
            url=candidate.locator,
            tier=candidate.tier_hint,
            adapter=self.name,
            checksum=content_hash,
            retrieved_at=datetime.now(),
        )
        artifact = FetchedArtifact(
            raw_path=raw_path,
            source=source,
            content_hash=content_hash,
        )
        write_sidecar(artifact)
        return artifact
