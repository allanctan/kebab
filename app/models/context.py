"""Vertical-specific context mapping.

Stored as a nested JSON blob in the Qdrant payload under ``contexts``. The
shape is intentionally open — consumers define their own keys (grade level,
jurisdiction, department, policy version, etc.). KEBAB never validates the
inner structure.
"""

from pydantic import BaseModel, ConfigDict


class ContextMapping(BaseModel):
    """Free-form container for vertical-specific filter data."""

    model_config = ConfigDict(extra="allow")
