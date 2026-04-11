"""Local-dataset adapter — wraps :mod:`app.pipeline.ingest.csv_json`."""

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
from app.pipeline.ingest import csv_json as csv_json_ingest

_DATASET_SUFFIXES = {".csv", ".json"}


@dataclass
class LocalDatasetAdapter:
    """Filesystem CSV/JSON source. Operator-curated → tier 1 by default."""

    settings: Settings
    name: ClassVar[str] = "local_dataset"
    default_tier: SourceTier = field(default=1)

    def discover(self, query: str, *, limit: int = 10) -> list[Candidate]:
        root = Path(query).expanduser()
        if not root.exists():
            return []
        if root.is_file() and root.suffix.lower() in _DATASET_SUFFIXES:
            return [self._candidate_from_path(root)]
        candidates: list[Candidate] = []
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in _DATASET_SUFFIXES:
                candidates.append(self._candidate_from_path(path))
                if len(candidates) >= limit:
                    break
        return candidates

    def _candidate_from_path(self, path: Path) -> Candidate:
        return Candidate(
            adapter=self.name,
            locator=str(path.resolve()),
            title=path.name,
            snippet=None,
            tier_hint=self.default_tier,
        )

    def fetch(self, candidate: Candidate) -> FetchedArtifact:
        if candidate.adapter != self.name:
            raise AdapterError(
                f"local_dataset cannot fetch candidate from adapter {candidate.adapter!r}"
            )
        path = Path(candidate.locator)
        if not path.exists():
            raise AdapterError(f"local_dataset: file not found: {path}")

        result = csv_json_ingest.ingest(self.settings, path)
        raw_path = result.target
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
        )
        write_sidecar(artifact)
        return artifact
