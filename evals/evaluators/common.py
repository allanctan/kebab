"""Shared judge factory for LLM-as-judge evaluators.

Following the design rules in the plan §M15:

- **Build judges per call**, never as module-level singletons.
  Prevents event-loop / mutation issues and lets the ``EVAL_MODEL``
  override take effect on every run.
- **Reasoning-before-verdict** ordering inside output models.
- **One judge per file, one concern per judge**.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent

from app.config.config import Settings
from app.core.llm.resolve import resolve_model

T = TypeVar("T", bound=BaseModel)


def build_judge_agent(
    output_model: type[T], system_prompt: str, settings: Settings
) -> Agent[None, T]:
    """Construct a fresh judge agent for ``output_model``.

    Always builds a *new* Agent — eval suites must avoid module-level
    singletons (better-ed-ai found these caused event-loop errors).
    """
    return Agent(
        model=resolve_model(settings.EVAL_MODEL),
        output_type=output_model,
        system_prompt=system_prompt,
        retries=settings.LLM_MAX_RETRIES,
    )
