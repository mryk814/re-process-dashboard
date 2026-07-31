"""Package loading orchestration over the isolated verification boundary."""

from __future__ import annotations

from pathlib import Path

from material_workbench.modeling.packages.contracts import (
    MAX_ARTIFACT_BYTES,
    MAX_PACKAGE_BYTES,
)
from material_workbench.modeling.packages.registry import AdapterRegistry
from material_workbench.modeling.packages.verification import (
    VerifiedModelPackage,
    verify_model_package,
)


class ModelPackageLoader:
    """Load only a verified snapshot through an application-owned registry."""

    def __init__(
        self,
        registry: AdapterRegistry | None = None,
        *,
        max_artifact_bytes: int = MAX_ARTIFACT_BYTES,
        max_package_bytes: int = MAX_PACKAGE_BYTES,
    ) -> None:
        self.registry = registry or AdapterRegistry()
        self.max_artifact_bytes = max_artifact_bytes
        self.max_package_bytes = max_package_bytes

    def load(self, package_root: str | Path) -> VerifiedModelPackage:
        return verify_model_package(
            package_root,
            registry=self.registry,
            max_artifact_bytes=self.max_artifact_bytes,
            max_package_bytes=self.max_package_bytes,
        )
