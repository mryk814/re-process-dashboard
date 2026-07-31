"""Shared immutable inputs for catalog use cases."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CatalogRuntimeState:
    resources_ready: bool
    resources_loading_error: str | None
    workspace_database: Path
    data_library_root: Path
    workspace_kind: str
    task_store_path: Path
    model_store_path: Path | None
