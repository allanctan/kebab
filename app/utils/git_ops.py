"""Git operations for agent-driven commits."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def commit(repo_path: Path, message: str, paths: list[Path]) -> None:
    """Stage and commit selected paths with a message.

    TODO: use ``git.Repo(repo_path)`` to add + commit.
    """
    raise NotImplementedError
