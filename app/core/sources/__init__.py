from app.core.sources.adapter import (
    SourceAdapter,
    Candidate,
    FetchedArtifact,
    AdapterError,
)
from app.core.sources.index import (
    SourceEntry,
    SourceIndex,
    load_index,
    save_index,
    register_source,
    extract_path_metadata,
)
from app.core.sources.provenance import (
    write_sidecar,
    read_sidecar,
    sha256_bytes,
    sha256_file,
    find_by_checksum,
    sidecar_path,
)
from app.core.sources.fetcher import (
    SharedFetcher,
    get_default_fetcher,
    user_agent,
    FetchError,
    FetchBlockedError,
    FetchTransientError,
)

__all__ = [
    "SourceAdapter",
    "Candidate",
    "FetchedArtifact",
    "AdapterError",
    "SourceEntry",
    "SourceIndex",
    "load_index",
    "save_index",
    "register_source",
    "extract_path_metadata",
    "write_sidecar",
    "read_sidecar",
    "sha256_bytes",
    "sha256_file",
    "find_by_checksum",
    "sidecar_path",
    "SharedFetcher",
    "get_default_fetcher",
    "user_agent",
    "FetchError",
    "FetchBlockedError",
    "FetchTransientError",
]
