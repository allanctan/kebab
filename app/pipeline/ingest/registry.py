"""Adapter registry — name → :class:`SourceAdapter` instance.

M17 foundation layer. Adapters register themselves here so the CLI and
the M21 research agent can look them up by name without importing each
adapter module explicitly. The registry is eager: all built-in adapters
are instantiated on first access and cached in the global singleton.

Built-in adapters:

- ``local_pdf`` — wraps :mod:`app.pipeline.ingest.pdf`.
- ``local_dataset`` — wraps :mod:`app.pipeline.ingest.csv_json`.
- ``direct_url`` — wraps :mod:`app.pipeline.ingest.web`.

Future adapters (Tavily, Wikipedia, OpenStax, …) plug in via
:meth:`AdapterRegistry.register`.
"""

import logging
from dataclasses import dataclass, field

from app.config.config import Settings
from app.core.errors import KebabError
from app.core.sources.adapter import SourceAdapter

logger = logging.getLogger(__name__)


@dataclass
class AdapterRegistry:
    """In-memory map of adapter name → adapter instance."""

    settings: Settings
    _adapters: dict[str, SourceAdapter] = field(default_factory=dict)

    def register(self, adapter: SourceAdapter) -> None:
        """Register ``adapter`` under its ``name``. Overwrites silently."""
        if not isinstance(adapter, SourceAdapter):
            raise KebabError(
                f"{type(adapter).__name__} does not satisfy the SourceAdapter protocol"
            )
        self._adapters[adapter.name] = adapter
        logger.debug("registry: registered adapter %r (tier=%d)", adapter.name, adapter.default_tier)

    def get(self, name: str) -> SourceAdapter:
        """Return the registered adapter, or raise :class:`KebabError`."""
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise KebabError(
                f"no adapter registered as {name!r} (known: {sorted(self._adapters)})"
            ) from exc

    def names(self) -> list[str]:
        """Return the sorted list of registered adapter names."""
        return sorted(self._adapters)


def build_default_registry(settings: Settings) -> AdapterRegistry:
    """Create a registry pre-loaded with every built-in adapter.

    Import-light: built-in adapter modules are imported lazily here so a
    downstream module importing the registry type does not drag in
    pymupdf / httpx transitively.
    """
    from app.pipeline.ingest.adapters.direct_url import DirectUrlAdapter
    from app.pipeline.ingest.adapters.local_pdf import LocalPdfAdapter
    from app.pipeline.ingest.adapters.openstax import OpenStaxAdapter
    from app.pipeline.ingest.adapters.tavily import TavilyAdapter
    from app.pipeline.ingest.adapters.wikipedia import WikipediaAdapter

    registry = AdapterRegistry(settings=settings)
    registry.register(LocalPdfAdapter(settings=settings))
    registry.register(DirectUrlAdapter(settings=settings))
    registry.register(TavilyAdapter(settings=settings))
    registry.register(WikipediaAdapter(settings=settings))
    registry.register(OpenStaxAdapter(settings=settings))
    return registry


__all__ = ["AdapterRegistry", "build_default_registry"]
