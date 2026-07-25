"""Explicit active/available catalog for deterministic transform Packages."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from material_workbench.adapters.builtin_deterministic_linear import (
    DeterministicLinearResult,
)
from material_workbench.contracts.blend_contracts import (
    CommercialMaterialCatalog,
    SparseBlend,
)
from material_workbench.contracts.stage_a_contracts import STAGE_A_COMPONENTS
from material_workbench.modeling.model_lifecycle import REPOSITORY_ROOT
from material_workbench.modeling.model_package_verify import (
    verify_deterministic_transform_package,
)
from material_workbench.modeling.model_packages import (
    ModelPackageLoader,
    PackageContractError,
    VerifiedModelPackage,
)


def _safe_relative_locator(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("transform locators must be relative to the models directory")
    return value.replace("\\", "/")


class _CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActiveTransformSelection(_CatalogModel):
    active: Annotated[str, Field(min_length=1)]
    available: Annotated[tuple[str, ...], Field(min_length=1)]
    commercial_catalog: Annotated[str, Field(min_length=1)]

    @field_validator("active", "commercial_catalog")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        return _safe_relative_locator(value)

    @field_validator("available")
    @classmethod
    def safe_available_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("available transform locators must be unique")
        return tuple(_safe_relative_locator(item) for item in value)

    @model_validator(mode="after")
    def active_is_available(self) -> "ActiveTransformSelection":
        if self.active not in self.available:
            raise ValueError("active transform locator must be listed as available")
        return self


class ActiveTransformsConfig(_CatalogModel):
    schema_version: Literal["active-deterministic-transforms/v1"]
    transforms: dict[str, ActiveTransformSelection]

    @field_validator("transforms")
    @classmethod
    def nonempty_ids(
        cls,
        value: dict[str, ActiveTransformSelection],
    ) -> dict[str, ActiveTransformSelection]:
        if not value or any(not transform_id for transform_id in value):
            raise ValueError("active deterministic transforms must be nonempty")
        return value


@dataclass(frozen=True)
class LoadedTransform:
    transform_id: str
    package: VerifiedModelPackage
    transform: Any
    commercial_catalog: CommercialMaterialCatalog
    package_locator: str
    available_package_locators: tuple[str, ...]
    commercial_catalog_locator: str


class DeterministicTransformCatalog:
    def __init__(self, entries: dict[str, LoadedTransform]) -> None:
        self._entries = entries

    @property
    def transform_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def entry(self, transform_id: str) -> LoadedTransform:
        try:
            return self._entries[transform_id]
        except KeyError as exc:
            raise KeyError(f"unknown deterministic transform: {transform_id}") from exc

    def execute(
        self,
        transform_id: str,
        blend: SparseBlend,
    ) -> DeterministicLinearResult:
        entry = self.entry(transform_id)
        return entry.transform.execute(blend, entry.commercial_catalog)


def active_transforms_path() -> Path:
    root = Path(os.getenv("WORKBENCH_RESOURCE_ROOT", str(REPOSITORY_ROOT)))
    return root / "models/active-transforms.json"


def _resolved_locator(models_root: Path, locator: str) -> Path:
    try:
        resolved = (models_root / locator).resolve(strict=True)
    except OSError as exc:
        raise PackageContractError(f"deterministic transform locator cannot be resolved: {locator}") from exc
    if models_root.resolve() not in resolved.parents:
        raise PackageContractError(f"deterministic transform locator escapes models root: {locator}")
    return resolved


def load_deterministic_transform_catalog(
    config_path: str | Path | None = None,
) -> DeterministicTransformCatalog:
    path = Path(config_path) if config_path is not None else active_transforms_path()
    try:
        config = ActiveTransformsConfig.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PackageContractError(f"invalid active deterministic transform catalog: {exc}") from exc
    models_root = path.resolve().parent
    entries: dict[str, LoadedTransform] = {}
    for transform_id, selection in config.transforms.items():
        loader = ModelPackageLoader()
        available_packages: dict[str, tuple[VerifiedModelPackage, Any]] = {}
        for locator in selection.available:
            package_root = _resolved_locator(models_root, locator)
            verify_deterministic_transform_package(package_root)
            available_package = loader.load(package_root)
            if available_package.manifest.package_kind != "deterministic_transform":
                raise PackageContractError(
                    f"available transform {locator} does not reference a deterministic Package"
                )
            if len(available_package.manifest.deterministic_transforms) != 1:
                raise PackageContractError(
                    f"available transform {locator} must expose exactly one transform"
                )
            if available_package.manifest.task_id != transform_id:
                raise PackageContractError(
                    f"available transform {locator} task does not match catalog id {transform_id}"
                )
            available_spec = available_package.manifest.deterministic_transforms[0]
            if available_spec.output_names != STAGE_A_COMPONENTS:
                raise PackageContractError(
                    f"available transform {locator} does not use the canonical Stage A axis"
                )
            available_packages[locator] = (
                available_package,
                available_package.load_transform(available_spec.id),
            )
        package, transform = available_packages[selection.active]
        spec = package.manifest.deterministic_transforms[0]
        active_signature = (
            spec.id,
            package.manifest.task_id,
            spec.runtime_type,
            spec.compiler_id,
            spec.output_names,
            spec.output_unit,
            spec.auxiliary_feature_names,
        )
        for locator, (available_package, _) in available_packages.items():
            available_spec = available_package.manifest.deterministic_transforms[0]
            available_signature = (
                available_spec.id,
                available_package.manifest.task_id,
                available_spec.runtime_type,
                available_spec.compiler_id,
                available_spec.output_names,
                available_spec.output_unit,
                available_spec.auxiliary_feature_names,
            )
            if available_signature != active_signature:
                raise PackageContractError(
                    f"available transform {locator} is incompatible with active transform "
                    f"{selection.active}"
                )
        catalog_path = _resolved_locator(models_root, selection.commercial_catalog)
        try:
            commercial_catalog = CommercialMaterialCatalog.model_validate_json(
                catalog_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise PackageContractError(
                f"invalid commercial catalog for transform {transform_id}: {exc}"
            ) from exc
        entries[transform_id] = LoadedTransform(
            transform_id=transform_id,
            package=package,
            transform=transform,
            commercial_catalog=commercial_catalog,
            package_locator=selection.active,
            available_package_locators=selection.available,
            commercial_catalog_locator=selection.commercial_catalog,
        )
    return DeterministicTransformCatalog(entries)
