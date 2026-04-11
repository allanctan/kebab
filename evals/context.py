"""Eval-side helpers — re-export model resolution so judges can be swapped.

Pattern adapted from ``better-ed-ai/evals/context.py``.
"""

from __future__ import annotations

from app.config.config import Settings
from app.core.llm.resolve import resolve_model
from app.core.llm.model_registry import list_aliases, resolve_alias

__all__ = ["resolve_eval_model", "list_aliases", "Settings"]


def resolve_eval_model(alias_or_model: str) -> object:
    """Resolve ``alias_or_model`` to a pydantic-ai-compatible model.

    Three accepted inputs:
    - A short alias defined in ``app/config/models.yaml`` (e.g. ``gemini-flash``).
    - A ``provider:model`` string (e.g. ``google-gla:gemini-2.5-pro``).
    - A ``$VAR`` reference resolved against :class:`Settings`.
    """
    if ":" in alias_or_model or alias_or_model.startswith("$"):
        return resolve_model(alias_or_model)
    return resolve_alias(alias_or_model)
