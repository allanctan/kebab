"""Generate package — chains contexts → gaps → write in one step."""

from __future__ import annotations

from app.agents.generate.contexts import ContextsResult as ContextsResult
from app.agents.generate.gaps import GapReport as GapReport
from app.agents.generate.generate import GenerateStageResult as GenerateStageResult
from app.agents.generate.generate import run as run
from app.agents.generate.writer import GenerateResult as GenerateResult
