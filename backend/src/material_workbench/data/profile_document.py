"""Compatibility imports for the centralized Profile family registry."""
from __future__ import annotations

from material_workbench.data.profile_family_registry import (
    lifecycle_profile_for_data,
    load_profile_document,
    profile_task_ids,
    supported_task_ids,
)

__all__ = (
    "lifecycle_profile_for_data",
    "load_profile_document",
    "profile_task_ids",
    "supported_task_ids",
)
