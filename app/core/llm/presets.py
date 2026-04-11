"""Operator-editable model alias resolution.

Reads ``app/config/models.yaml`` and exposes :func:`resolve_alias` and
:func:`list_aliases`. Pattern adapted from
``better-ed-ai/app/config/model_registry.py``.

Why a YAML file (and not the ``MODEL_PRESETS = {...}`` dict in the original
plan): operators can add aliases without editing Python, and ``${VAR}``
references inside an entry are **lazy-expanded** at use time — adding a
Bedrock alias never breaks startup just because ``BEDROCK_REGION`` is
unset for users who only run Gemini.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.errors import ConfigError

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "models.yaml"


@dataclass(frozen=True)
class ModelEntry:
    """One row of ``models.yaml``."""

    alias: str
    provider: str
    model: str
    endpoint: str | None = None
    api_key: str | None = None
    api_version: str | None = None

    @property
    def model_string(self) -> str:
        """Return the ``provider:model`` string for native pydantic-ai."""
        return f"{self.provider}:{self.model}"

    @property
    def is_custom_endpoint(self) -> bool:
        return self.endpoint is not None


@lru_cache(maxsize=1)
def _load_registry() -> dict[str, ModelEntry]:
    """Load and cache ``models.yaml`` once per process."""
    if not _REGISTRY_PATH.exists():
        raise ConfigError(f"models.yaml not found at {_REGISTRY_PATH}")
    raw = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    rows = raw.get("models", [])
    if not isinstance(rows, list):
        raise ConfigError("models.yaml: top-level 'models' must be a list")
    registry: dict[str, ModelEntry] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ConfigError(f"models.yaml: each entry must be a mapping, got {type(row)}")
        try:
            entry = ModelEntry(
                alias=row["alias"],
                provider=row["provider"],
                model=row["model"],
                endpoint=row.get("endpoint"),
                api_key=row.get("api_key"),
                api_version=row.get("api_version"),
            )
        except KeyError as exc:
            raise ConfigError(
                f"models.yaml: entry missing required field {exc}"
            ) from exc
        if entry.alias in registry:
            raise ConfigError(f"models.yaml: duplicate alias '{entry.alias}'")
        registry[entry.alias] = entry
    return registry


def list_aliases() -> list[str]:
    """Return all known aliases (sorted)."""
    return sorted(_load_registry().keys())


def get_entry(alias: str) -> ModelEntry | None:
    """Return the :class:`ModelEntry` for ``alias`` or ``None``."""
    return _load_registry().get(alias)


def resolve_alias(alias: str) -> Any:
    """Resolve an alias to a pydantic-ai-compatible model.

    - If the alias is unknown, raise :class:`ConfigError` (no silent
      fall-through to a broken string).
    - Native-prefix entries return the ``provider:model`` string.
    - Custom-endpoint entries call :func:`build_endpoint_model` with
      lazy-expanded ``${VAR}`` references.
    """
    entry = get_entry(alias)
    if entry is None:
        known = ", ".join(list_aliases()) or "(none)"
        raise ConfigError(f"unknown model alias '{alias}'. Known: {known}")
    if entry.provider == "bedrock":
        return _build_bedrock(entry)
    if entry.is_custom_endpoint:
        # Local import avoids a cycle: llm.resolve_model → resolve_alias → build_endpoint_model.
        from app.core.llm.resolve import build_endpoint_model

        return build_endpoint_model(
            entry.model_string,
            endpoint=entry.endpoint or "",
            api_key_ref=entry.api_key,
            api_version=entry.api_version,
        )
    return entry.model_string


def _build_bedrock(entry: ModelEntry) -> Any:
    """Build a BedrockConverseModel using AWS credentials from Settings."""
    from app.config import env

    if not env.AWS_ACCESS_KEY_ID or not env.AWS_SECRET_ACCESS_KEY:
        raise ConfigError(
            f"Bedrock alias '{entry.alias}' requires AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY in .env.local"
        )
    from pydantic_ai.models.bedrock import BedrockConverseModel
    from pydantic_ai.providers.bedrock import BedrockProvider

    provider = BedrockProvider(
        aws_access_key_id=env.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=env.AWS_SECRET_ACCESS_KEY,
        region_name=env.AWS_REGION,
    )
    return BedrockConverseModel(entry.model, provider=provider)


def reload_registry() -> None:
    """Drop the cached registry — useful in tests."""
    _load_registry.cache_clear()
