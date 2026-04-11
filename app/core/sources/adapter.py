"""Source adapter protocol — the contract every acquisition channel implements.

M17 foundation layer. An *adapter* knows how to find and fetch source
material from a single channel (local PDF, local CSV, direct URL, Tavily
search, Wikipedia, OpenStax, …). Downstream pipeline stages
(``organize``/``gaps``/``generate``) never see adapter specifics — they
only see the files the adapter landed under ``raw/``.

Every adapter:

1. Declares a stable ``name`` and a ``default_tier`` (1–5).
2. Exposes ``discover(query, limit)`` — returns :class:`Candidate` refs
   without fetching bytes. Cheap and idempotent.
3. Exposes ``fetch(candidate)`` — writes bytes under
   ``settings.RAW_DIR`` and returns a :class:`FetchedArtifact` with a
   fully-populated provenance envelope.

The two-step ``discover → fetch`` split lets humans review candidates
before committing to a fetch (the M18+ UX) and lets the M21 research
agent call adapters as tools.

Not every adapter supports both steps — local-file adapters treat
``discover`` as "list files under a path", and direct-URL adapters
treat it as a no-op that returns a single candidate built from the URL.
"""

from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import KebabError
from app.models.source import Source, SourceTier


class AdapterError(KebabError):
    """Raised when an adapter cannot fetch or parse a candidate."""


class Candidate(BaseModel):
    """A pre-fetch reference to something an adapter could retrieve.

    Adapters return candidates from ``discover()``. Candidates carry
    enough metadata to display to a human for approval but no bytes:
    fetching happens only on ``fetch(candidate)``.
    """

    model_config = ConfigDict(extra="forbid")

    adapter: str = Field(..., description="Name of the adapter that produced this candidate.")
    locator: str = Field(
        ...,
        description="Adapter-specific identifier — URL, file path, DOI, Wikipedia title, "
        "OpenStax book id, etc. Must be stable enough for ``fetch()`` to retrieve the same item.",
    )
    title: str = Field(..., description="Human-readable title.")
    snippet: str | None = Field(
        default=None,
        description="Short preview used during human review. May be empty.",
    )
    tier_hint: SourceTier = Field(
        ...,
        description="Tier this candidate *would* be assigned if fetched. "
        "Usually the adapter's default tier; may be bumped by discovery heuristics.",
    )


class FetchedArtifact(BaseModel):
    """Result of a successful ``adapter.fetch()`` call.

    The adapter guarantees that:
    - ``raw_path`` exists and contains the raw bytes the adapter fetched.
    - ``source`` has ``tier``, ``checksum``, ``retrieved_at``, and
      ``adapter`` populated (other fields optional).
    - ``content_hash`` is the SHA256 of ``raw_path``'s bytes.
    """

    model_config = ConfigDict(extra="forbid")

    raw_path: Path = Field(..., description="Filesystem path of the stored raw artifact.")
    source: Source = Field(..., description="Populated provenance envelope for this artifact.")
    content_hash: str = Field(..., description="SHA256 hex digest of the raw bytes.")
    license: str | None = Field(
        default=None,
        description="Upstream license identifier if known (e.g. 'CC-BY-4.0', 'public-domain').",
    )


@runtime_checkable
class SourceAdapter(Protocol):
    """Protocol every source-gathering adapter must satisfy.

    ``runtime_checkable`` so tests can assert conformance with
    ``isinstance(adapter, SourceAdapter)``. ``name`` is marked
    :class:`ClassVar` because adapter names are compile-time constants,
    never per-instance.
    """

    name: ClassVar[str]
    default_tier: SourceTier

    def discover(self, query: str, *, limit: int = 10) -> list[Candidate]:
        """Return up to ``limit`` candidates matching ``query``.

        Implementations should be cheap and idempotent. They SHOULD NOT
        fetch bytes — that's ``fetch()``'s job.
        """
        ...

    def fetch(self, candidate: Candidate) -> FetchedArtifact:
        """Retrieve the candidate and persist its bytes under ``raw/``.

        Raises :class:`AdapterError` on any unrecoverable failure (bad
        URL, parse error, license violation, etc.).
        """
        ...


__all__ = [
    "AdapterError",
    "Candidate",
    "FetchedArtifact",
    "SourceAdapter",
]
