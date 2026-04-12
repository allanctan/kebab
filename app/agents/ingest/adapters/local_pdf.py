"""Local-PDF adapter — wraps :mod:`app.agents.ingest.pdf`.

``discover(path)`` walks a filesystem path and returns one candidate per
PDF found. ``fetch(candidate)`` delegates to the legacy ``pdf.ingest()``
function and stamps a provenance sidecar next to the copied raw file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
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
from app.agents.ingest import pdf as pdf_ingest


@dataclass
class LocalPdfAdapter:
    """Filesystem PDF source. Authoritative for whatever tier the operator picks."""

    settings: Settings
    name: ClassVar[str] = "local_pdf"
    default_tier: SourceTier = field(default=1)  # operator-curated content → tier 1

    def discover(self, query: str, *, limit: int = 10) -> list[Candidate]:
        """List PDF files under ``query`` (a filesystem path).

        ``query`` is interpreted as a path. If it points at a single PDF,
        one candidate is returned; if it's a directory, all PDFs under it
        are returned up to ``limit``.
        """
        root = Path(query).expanduser()
        if not root.exists():
            return []
        if root.is_file() and root.suffix.lower() == ".pdf":
            return [self._candidate_from_path(root)]
        candidates: list[Candidate] = []
        for path in sorted(root.rglob("*.pdf")):
            candidates.append(self._candidate_from_path(path))
            if len(candidates) >= limit:
                break
        return candidates

    def _candidate_from_path(self, path: Path) -> Candidate:
        return Candidate(
            adapter=self.name,
            locator=str(path.resolve()),
            title=path.stem.replace("_", " ").replace("-", " "),
            snippet=None,
            tier_hint=self.default_tier,
        )

    def fetch(self, candidate: Candidate) -> FetchedArtifact:
        """Copy the PDF into ``raw/documents/`` and stamp a provenance sidecar."""
        if candidate.adapter != self.name:
            raise AdapterError(
                f"local_pdf cannot fetch candidate from adapter {candidate.adapter!r}"
            )
        path = Path(candidate.locator)
        if not path.exists():
            raise AdapterError(f"local_pdf: file not found: {path}")

        result = pdf_ingest.ingest(self.settings, path)
        raw_path = result.original
        content_hash = sha256_file(raw_path)
        source = Source(
            id=0,
            title=candidate.title,
            tier=candidate.tier_hint,
            adapter=self.name,
            checksum=content_hash,
            retrieved_at=datetime.now(),
        )
        artifact = FetchedArtifact(
            raw_path=raw_path,
            source=source,
            content_hash=content_hash,
            license=None,
        )
        write_sidecar(artifact)
        return artifact
