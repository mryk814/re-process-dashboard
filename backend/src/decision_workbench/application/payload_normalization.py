"""Normalize application payloads without coupling producers to consumers."""
from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel


def plain_payload(value: Any) -> Any:
    """Return JSON-compatible containers while preserving scalar values."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): plain_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain_payload(item) for item in value]
    return value
