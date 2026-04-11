"""Sidecar provenance — `<raw_file>.meta.json` next to every raw artifact.

Every :class:`FetchedArtifact` an adapter produces should have a
matching ``.meta.json`` sidecar so operators can see, without re-running
the pipeline, *where* the bytes came from and *when*. Sidecars are also
the basis for content-hash dedup: an adapter can check whether a
candidate has already been fetched by scanning sidecars for a matching
checksum before doing any network I/O.

Sidecar format (JSON):

.. code-block:: json

    {
      "adapter": "local_pdf",
      "locator": "/Users/daisy/pdfs/photosynthesis.pdf",
      "content_hash": "ab12…",
      "retrieved_at": "2026-04-08T13:22:00",
      "license": null,
      "source": {
        "title": "Photosynthesis (DepEd Q2 M1)",
        "url": null,
        "tier": 1,
        "adapter": "local_pdf",
        "checksum": "ab12…",
        "retrieved_at": "2026-04-08T13:22:00"
      }
    }

This is the *only* persistence layer for full provenance — neither the
PDF itself nor ``processed/documents/<stem>/`` carries the envelope.
Curated articles get a condensed form of this via
``frontmatter.sources``; the full sidecar stays with the raw artifact.
"""

import hashlib
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.core.sources.adapter import FetchedArtifact

logger = logging.getLogger(__name__)

_SIDECAR_SUFFIX = ".meta.json"


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hex SHA256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Stream ``path`` through SHA256 without loading it entirely."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sidecar_path(raw_path: Path) -> Path:
    """Return the sidecar path for a raw artifact."""
    return raw_path.with_name(raw_path.name + _SIDECAR_SUFFIX)


def write_sidecar(artifact: FetchedArtifact) -> Path:
    """Write ``artifact`` as a JSON sidecar next to its raw bytes.

    Overwrites any existing sidecar at that path. Returns the sidecar
    path for logging.
    """
    target = sidecar_path(artifact.raw_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = artifact.model_dump(mode="json", exclude_none=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    logger.debug("provenance: wrote sidecar %s", target)
    return target


def read_sidecar(raw_path: Path) -> FetchedArtifact | None:
    """Load the sidecar for ``raw_path``, or return ``None`` if absent/invalid.

    Invalid sidecars are logged and treated as missing so a corrupted
    file can't block the pipeline — the caller will just re-fetch.
    """
    target = sidecar_path(raw_path)
    if not target.exists():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return FetchedArtifact.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("provenance: ignoring invalid sidecar %s: %s", target, exc)
        return None


def find_by_checksum(search_dir: Path, checksum: str) -> Path | None:
    """Scan ``search_dir`` for a sidecar whose ``content_hash`` matches.

    Returns the raw artifact path (not the sidecar path) if found, else
    ``None``. Used for dedup: before downloading a candidate, adapters
    can check whether an identical file has already been fetched.
    """
    if not search_dir.exists():
        return None
    for meta in search_dir.rglob(f"*{_SIDECAR_SUFFIX}"):
        artifact = read_sidecar(meta.with_name(meta.name.removesuffix(_SIDECAR_SUFFIX)))
        if artifact is None:
            continue
        if artifact.content_hash == checksum:
            return artifact.raw_path
    return None


__all__ = [
    "find_by_checksum",
    "read_sidecar",
    "sha256_bytes",
    "sha256_file",
    "sidecar_path",
    "write_sidecar",
]
