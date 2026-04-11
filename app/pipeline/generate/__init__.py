"""Generate package — chains contexts → gaps → write in one step."""

from __future__ import annotations

from app.pipeline.generate.contexts import ContextsResult as ContextsResult
from app.pipeline.generate.gaps import GapReport as GapReport
from app.pipeline.generate.generate import GenerateStageResult as GenerateStageResult
from app.pipeline.generate.generate import run as run
from app.pipeline.generate.writer import GenerateResult as GenerateResult
