"""Model-string → pydantic-ai Model resolution.

Pattern adapted from
``better-ed-ai/app/core/agent_factory.py`` (lines 64–226). The shape:

1. ``_FACTORIES`` — dict of ``prefix → ModelFactory`` populated by ``@_register``.
2. ``_expand_env(ref)`` — expand ``$VAR`` and ``${VAR}`` against
   :class:`Settings`. Lazy: only resolved when an alias is actually used,
   so missing credentials for unused aliases don't break startup.
3. ``resolve_model(model_string)`` — three-tier dispatch:
       env reference → ``provider:model`` factory → YAML alias lookup.
4. ``build_endpoint_model(...)`` — explicit endpoint + key for
   OpenAI-compatible providers (Azure, MiniMax, GLM, …).

Anti-patterns intentionally NOT copied from better-ed-ai:
- No silent fallback when a `$VAR` resolves to empty — we raise.
- No dynamic system-prompt placeholder injection (KEBAB prompts are static).
- No threading singletons — KEBAB is single-process CLI.

M1 registers only native pydantic-ai prefixes (``google-gla``, ``openai``,
``anthropic``). Custom OpenAI-compat factories (Bedrock, Azure, MiniMax,
GLM) can be added later with the same ``@_register`` decorator.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from app.config import env
from app.core.errors import ConfigError

logger = logging.getLogger(__name__)

ModelFactory = Callable[[str], Any]
_FACTORIES: dict[str, ModelFactory] = {}

_NATIVE_PREFIXES = frozenset({"google-gla", "openai", "anthropic"})


def _register(prefix: str) -> Callable[[ModelFactory], ModelFactory]:
    """Register ``fn`` as the factory for ``prefix``."""

    def decorator(fn: ModelFactory) -> ModelFactory:
        _FACTORIES[prefix] = fn
        return fn

    return decorator


import re as _re

_ENV_REF_RE = _re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}|\$([A-Z_][A-Z0-9_]*)")


def _expand_env(ref: str | None) -> str | None:
    """Resolve ``$VAR`` or ``${VAR}`` references against :class:`Settings`.

    Supports inline references like ``${ENDPOINT}openai/v1/`` — the
    ``${VAR}`` portion is expanded and the suffix is preserved. Multiple
    references in one string are expanded left to right.

    Returns ``ref`` verbatim when it contains no ``$``. Raises
    :class:`ConfigError` when a referenced setting is missing or empty.
    """
    if not ref:
        return ref
    if "$" not in ref:
        return ref

    def _sub(match: _re.Match[str]) -> str:
        var_name = match.group(1) or match.group(2)
        value = getattr(env, var_name, None)
        if not value:
            raise ConfigError(
                f"model reference '{ref}' points to unknown or empty setting '{var_name}'"
            )
        return str(value)

    return _ENV_REF_RE.sub(_sub, ref)


_PROVIDER_ENV = {
    "google-gla": ("GOOGLE_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY", "OPENAI_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
}


def _ensure_provider_env(prefix: str) -> None:
    """Mirror KEBAB Settings credentials into OS env so pydantic-ai can see them.

    pydantic-ai providers read credentials from ``os.environ`` directly.
    KEBAB stores them in :class:`Settings` (prefixed with ``KEBAB_``), so we
    export the matching unprefixed name into the process env the first time
    a model of this prefix is resolved. No-op if the OS env var is already set.
    """
    mapping = _PROVIDER_ENV.get(prefix)
    if mapping is None:
        return
    os_var, settings_attr = mapping
    if os.environ.get(os_var):
        return
    value = getattr(env, settings_attr, "")
    if value:
        os.environ[os_var] = str(value)


def resolve_model(model_string: str) -> Any:
    """Resolve a model string to a value pydantic-ai's ``Agent(model=...)`` accepts.

    Three-tier dispatch:
        1. ``$VAR`` / ``${VAR}`` references are expanded first.
        2. ``provider:model`` strings dispatch to a registered factory if any,
           otherwise pass through verbatim (pydantic-ai understands native
           prefixes itself).
        3. Bare aliases fall through to ``model_presets.resolve_alias``.
    """
    expanded = _expand_env(model_string) or model_string
    if ":" in expanded:
        prefix = expanded.split(":", 1)[0]
        _ensure_provider_env(prefix)
        factory = _FACTORIES.get(prefix)
        if factory is not None:
            return factory(expanded)
        if prefix in _NATIVE_PREFIXES:
            return expanded
        logger.debug("unknown prefix %r — passing model string through verbatim", prefix)
        return expanded
    # No colon → treat as alias.
    from app.core.llm.model_registry import resolve_alias

    return resolve_alias(expanded)


def build_endpoint_model(
    model_string: str,
    endpoint: str,
    api_key_ref: str | None,
    *,
    api_version: str | None = None,
) -> Any:
    """Build a pydantic-ai model bound to a custom OpenAI-compatible endpoint.

    Pattern from ``better-ed-ai/app/core/agent_factory.py::build_endpoint_model``
    (lines 192–226). All ``$VAR`` references are expanded before construction.
    """
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    resolved_endpoint = _expand_env(endpoint) or endpoint
    api_key = _expand_env(api_key_ref)
    model_name = model_string.split(":", 1)[1] if ":" in model_string else model_string

    provider_kwargs: dict[str, Any] = {"base_url": resolved_endpoint}
    if api_key:
        provider_kwargs["api_key"] = api_key
    if api_version:
        provider_kwargs["api_version"] = api_version
    provider = OpenAIProvider(**provider_kwargs)
    return OpenAIChatModel(model_name, provider=provider)
