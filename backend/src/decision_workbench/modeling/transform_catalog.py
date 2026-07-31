"""Explicit active/available catalog for deterministic transform Packages."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from decision_workbench.adapters.builtin_deterministic_linear import (
    DeterministicLinearResult,
)
from decision_workbench.contracts.blend_contracts import (
    BlendMaterialDescriptor,
    BlendStructuralError,
    CommercialMaterialCatalog,
    RevisionRef,
    ResolvedBlendContracts,
    SparseBlend,
    SparseBlendDesignSpace,
    describe_blend_materials,
)
from decision_workbench.contracts.stage_a_contracts import (
    STAGE_A_AUXILIARY_SOURCE_PRESENTATION,
    STAGE_A_COMPONENT_OUTPUT_UNIT,
    STAGE_A_COMPONENTS,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.modeling.model_lifecycle import REPOSITORY_ROOT
from decision_workbench.modeling.model_package_verify import (
    verify_deterministic_transform_package,
)
from decision_workbench.modeling.packages.contracts import PackageContractError
from decision_workbench.modeling.packages.loader import ModelPackageLoader
from decision_workbench.modeling.packages.verification import VerifiedModelPackage


class DeterministicTransformCatalogUnavailableError(PackageContractError):
    """The optional active Transform catalog cannot describe an available resource."""


class UnsafeTransformLocatorError(ValueError):
    """A Transform locator attempts to leave the trusted models root."""


def _safe_relative_locator(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise UnsafeTransformLocatorError(
            "transform locators must be relative to the models directory"
        )
    return value.replace("\\", "/")


class _CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActiveTransformSelection(_CatalogModel):
    active: Annotated[str, Field(min_length=1)]
    available: Annotated[tuple[str, ...], Field(min_length=1)]
    commercial_catalog: Annotated[str, Field(min_length=1)]
    available_commercial_catalogs: Annotated[tuple[str, ...], Field(min_length=1)]
    design_space: Annotated[str, Field(min_length=1)]
    available_design_spaces: Annotated[tuple[str, ...], Field(min_length=1)]

    @field_validator("active", "commercial_catalog", "design_space")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        return _safe_relative_locator(value)

    @field_validator("available")
    @classmethod
    def safe_available_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("available transform locators must be unique")
        return tuple(_safe_relative_locator(item) for item in value)

    @field_validator("available_commercial_catalogs", "available_design_spaces")
    @classmethod
    def safe_available_resource_paths(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("available resource locators must be unique")
        return tuple(_safe_relative_locator(item) for item in value)

    @model_validator(mode="after")
    def active_is_available(self) -> "ActiveTransformSelection":
        if self.active not in self.available:
            raise ValueError("active transform locator must be listed as available")
        if self.commercial_catalog not in self.available_commercial_catalogs:
            raise ValueError("active commercial catalog must be listed as available")
        if self.design_space not in self.available_design_spaces:
            raise ValueError("active Design Space must be listed as available")
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
    design_space: SparseBlendDesignSpace
    package_locator: str
    available_package_locators: tuple[str, ...]
    commercial_catalog_locator: str
    design_space_locator: str


@dataclass(frozen=True)
class HistoricalTransformResolution:
    transform_id: str
    package: VerifiedModelPackage
    contract_digest: str
    transform: Any
    contracts: ResolvedBlendContracts


@dataclass(frozen=True)
class DeterministicOutputPresentation:
    key: str
    label: str
    unit: str
    display_decimals: int


def deterministic_transform_contract_digest(
    package: VerifiedModelPackage,
) -> str:
    spec = package.manifest.deterministic_transforms[0]
    return semantic_digest(
        {
            "schema_version": "deterministic-transform-contract/v1",
            "task_id": package.manifest.task_id,
            "input_schema_version": package.manifest.input_schema_version,
            "transform": spec.model_dump(mode="json"),
        }
    )


class DeterministicTransformCatalog:
    def __init__(
        self,
        entries: dict[str, LoadedTransform],
        historical: dict[
            tuple[RevisionRef, RevisionRef, RevisionRef],
            HistoricalTransformResolution,
        ],
        historical_by_package: tuple[HistoricalTransformResolution, ...] | None = None,
    ) -> None:
        self._entries = entries
        self._historical = historical
        self._historical_by_package = (
            historical_by_package
            if historical_by_package is not None
            else tuple(historical.values())
        )

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
        self.entry(transform_id)
        resolution = self._resolution_for(blend)
        return resolution.transform.execute(
            blend,
            resolution.contracts.commercial_catalog,
        )

    def initial_blend(self, transform_id: str) -> SparseBlend:
        """Create a valid, editable starting point pinned to the active resources."""
        entry = self.entry(transform_id)
        space = entry.design_space
        return SparseBlend(
            items=(
                {
                    "material_id": space.balance_material_id,
                    "ratio": space.total,
                },
            ),
            hoop_id=space.fixed_hoop_id,
            fill_ratio=space.fixed_fill_ratio,
            balance_material_id=space.balance_material_id,
            scientific_master=entry.transform.artifact.scientific_master.ref,
            commercial_catalog=entry.commercial_catalog.ref,
            design_space=space.ref,
        )

    def resolve_blend(self, blend: SparseBlend) -> ResolvedBlendContracts:
        return self._resolution_for(blend).contracts

    def resolve_execution(
        self,
        transform_id: str,
        blend: SparseBlend,
        package_manifest_digest: str | None = None,
        contract_digest: str | None = None,
    ) -> HistoricalTransformResolution:
        candidates = [
            resolution
            for resolution in self._historical_by_package
            if resolution.transform_id == transform_id
            and resolution.contracts.scientific_master.ref
            == blend.scientific_master
            and resolution.contracts.commercial_catalog.ref
            == blend.commercial_catalog
            and resolution.contracts.design_space.ref == blend.design_space
            and (
                package_manifest_digest is None
                or f"sha256:{resolution.package.manifest_sha256}"
                == package_manifest_digest
            )
            and (
                contract_digest is None
                or resolution.contract_digest == contract_digest
            )
        ]
        if not candidates:
            raise BlendStructuralError(
                "配合とStage A Package/contractの完全一致revisionが見つかりません"
            )
        if package_manifest_digest is None:
            active_digest = f"sha256:{self.entry(transform_id).package.manifest_sha256}"
            active = [
                item
                for item in candidates
                if f"sha256:{item.package.manifest_sha256}" == active_digest
            ]
            if active:
                return active[0]
        return candidates[0]

    def initial_blend_for_package(
        self,
        transform_id: str,
        package_manifest_digest: str,
        contract_digest: str,
    ) -> SparseBlend:
        candidates = [
            resolution
            for resolution in self._historical_by_package
            if resolution.transform_id == transform_id
            and f"sha256:{resolution.package.manifest_sha256}"
            == package_manifest_digest
            and resolution.contract_digest == contract_digest
        ]
        if not candidates:
            raise BlendStructuralError(
                "Chain Revisionに固定されたStage A Package/contractが"
                "historical registryに見つかりません"
            )
        resolution = max(
            candidates,
            key=lambda item: (
                item.contracts.design_space.revision,
                item.contracts.commercial_catalog.revision,
            ),
        )
        space = resolution.contracts.design_space
        return SparseBlend(
            items=(
                {
                    "material_id": space.balance_material_id,
                    "ratio": space.total,
                },
            ),
            hoop_id=space.fixed_hoop_id,
            fill_ratio=space.fixed_fill_ratio,
            balance_material_id=space.balance_material_id,
            scientific_master=space.scientific_master,
            commercial_catalog=space.commercial_catalog,
            design_space=space.ref,
        )

    def resolution_for_revision(
        self,
        transform_id: str,
        package_manifest_digest: str,
        contract_digest: str,
    ) -> HistoricalTransformResolution:
        """Resolve immutable Package and adapter state pinned by a Chain Stage."""

        candidates = [
            resolution
            for resolution in self._historical_by_package
            if resolution.transform_id == transform_id
            and f"sha256:{resolution.package.manifest_sha256}"
            == package_manifest_digest
            and resolution.contract_digest == contract_digest
        ]
        if not candidates:
            raise BlendStructuralError(
                "Chain Revisionに固定されたStage A Package/contractが"
                "historical registryに見つかりません"
            )
        return candidates[0]

    def output_presentations_for_revision(
        self,
        transform_id: str,
        package_manifest_digest: str,
        contract_digest: str,
    ) -> tuple[DeterministicOutputPresentation, ...]:
        """Resolve display metadata from the exact pinned deterministic adapter."""

        resolution = self.resolution_for_revision(
            transform_id,
            package_manifest_digest,
            contract_digest,
        )
        spec = resolution.package.manifest.deterministic_transforms[0]
        presentations = [
            DeterministicOutputPresentation(
                key=key,
                label=key,
                unit=STAGE_A_COMPONENT_OUTPUT_UNIT,
                display_decimals=3,
            )
            for key in spec.output_names
        ]
        features = {
            feature.name: feature
            for feature in resolution.transform.artifact.auxiliary_features
        }
        for key in spec.auxiliary_feature_names:
            feature = features.get(key)
            presentation = (
                STAGE_A_AUXILIARY_SOURCE_PRESENTATION.get(feature.source)
                if feature is not None
                else None
            )
            if presentation is None:
                raise BlendStructuralError(
                    f"deterministic transform output presentation is missing: {key}"
                )
            label, unit, display_decimals = presentation
            presentations.append(
                DeterministicOutputPresentation(
                    key=key,
                    label=label,
                    unit=unit,
                    display_decimals=display_decimals,
                )
            )
        return tuple(presentations)

    def _resolution_for(self, blend: SparseBlend) -> HistoricalTransformResolution:
        try:
            return self._historical[
                (
                    blend.scientific_master,
                    blend.commercial_catalog,
                    blend.design_space,
                )
            ]
        except KeyError as exc:
            raise BlendStructuralError(
                "配合が参照するStage A・商用catalog・Design Spaceの"
                "完全一致revisionが見つかりません"
            ) from exc

    def describe_blend(
        self,
        blend: SparseBlend,
    ) -> tuple[BlendMaterialDescriptor, ...]:
        return describe_blend_materials(blend, self.resolve_blend(blend))


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
    except ValidationError as exc:
        if any(
            isinstance(error.get("ctx", {}).get("error"), UnsafeTransformLocatorError)
            for error in exc.errors(include_url=False)
        ):
            raise PackageContractError(
                f"unsafe active deterministic transform locator: {exc}"
            ) from exc
        raise DeterministicTransformCatalogUnavailableError(
            f"invalid active deterministic transform catalog: {exc}"
        ) from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise DeterministicTransformCatalogUnavailableError(
            f"invalid active deterministic transform catalog: {exc}"
        ) from exc
    models_root = path.resolve().parent
    entries: dict[str, LoadedTransform] = {}
    historical: dict[
        tuple[RevisionRef, RevisionRef, RevisionRef],
        HistoricalTransformResolution,
    ] = {}
    historical_by_package: list[HistoricalTransformResolution] = []
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
        available_catalogs: dict[str, CommercialMaterialCatalog] = {}
        for locator in selection.available_commercial_catalogs:
            catalog_path = _resolved_locator(models_root, locator)
            try:
                available_catalogs[locator] = (
                    CommercialMaterialCatalog.model_validate_json(
                        catalog_path.read_text(encoding="utf-8")
                    )
                )
            except (OSError, ValueError) as exc:
                raise PackageContractError(
                    f"invalid commercial catalog for transform {transform_id}: {exc}"
                ) from exc
        commercial_catalog = available_catalogs[selection.commercial_catalog]
        if commercial_catalog.schema_version != "commercial-material-catalog/v2":
            raise PackageContractError(
                f"active commercial catalog for transform {transform_id} must be v2"
            )

        available_design_spaces: dict[str, SparseBlendDesignSpace] = {}
        for locator in selection.available_design_spaces:
            design_space_path = _resolved_locator(models_root, locator)
            try:
                available_design_spaces[locator] = (
                    SparseBlendDesignSpace.model_validate_json(
                        design_space_path.read_text(encoding="utf-8")
                    )
                )
            except (OSError, ValueError) as exc:
                raise PackageContractError(
                    f"invalid Design Space for transform {transform_id}: {exc}"
                ) from exc
        design_space = available_design_spaces[selection.design_space]

        packages_by_science: dict[
            RevisionRef,
            list[tuple[VerifiedModelPackage, Any]],
        ] = {}
        for available_package, loaded_transform in available_packages.values():
            packages_by_science.setdefault(
                loaded_transform.artifact.scientific_master.ref, []
            ).append((available_package, loaded_transform))
        catalogs_by_ref = {
            available_catalog.ref: available_catalog
            for available_catalog in available_catalogs.values()
        }
        for historical_space in available_design_spaces.values():
            historical_packages_and_transforms = packages_by_science.get(
                historical_space.scientific_master
            )
            historical_catalog = catalogs_by_ref.get(
                historical_space.commercial_catalog
            )
            if not historical_packages_and_transforms or historical_catalog is None:
                raise PackageContractError(
                    f"Design Space for transform {transform_id} does not reference "
                    "an available scientific/commercial revision"
                )
            historical_package, historical_transform = next(
                (
                    item
                    for item in historical_packages_and_transforms
                    if item[0].manifest_sha256 == package.manifest_sha256
                ),
                historical_packages_and_transforms[0],
            )
            scientific_by_id = {
                material.material_id: material
                for material in historical_transform.artifact.scientific_master.materials
            }
            commercial_by_id = {
                material.material_id: material
                for material in historical_catalog.materials
            }
            if set(scientific_by_id) != set(commercial_by_id):
                raise PackageContractError(
                    f"commercial catalog for transform {transform_id} must cover "
                    "the exact scientific material set"
                )
            if historical_catalog.schema_version == "commercial-material-catalog/v2":
                if any(
                    commercial_by_id[material_id].group != scientific.group
                    for material_id, scientific in scientific_by_id.items()
                ):
                    raise PackageContractError(
                        f"commercial catalog for transform {transform_id} changes "
                        "a scientific material group"
                    )
                if any(
                    not set(material.main_components or ()) <= set(STAGE_A_COMPONENTS)
                    for material in historical_catalog.materials
                ):
                    raise PackageContractError(
                        f"commercial catalog for transform {transform_id} has "
                        "unknown main components"
                    )
            if not set(historical_space.allowed_material_ids) <= set(scientific_by_id):
                raise PackageContractError(
                    f"Design Space for transform {transform_id} contains unknown materials"
                )
            contracts = ResolvedBlendContracts(
                historical_transform.artifact.scientific_master,
                historical_catalog,
                historical_space,
            )
            key = (
                historical_space.scientific_master,
                historical_space.commercial_catalog,
                historical_space.ref,
            )
            if key in historical:
                raise PackageContractError(
                    "duplicate immutable deterministic transform resource combination"
                )
            default_resolution = HistoricalTransformResolution(
                transform_id=transform_id,
                package=historical_package,
                contract_digest=deterministic_transform_contract_digest(
                    historical_package
                ),
                transform=historical_transform,
                contracts=contracts,
            )
            historical[key] = default_resolution
            exact_keys: set[tuple[str, str]] = set()
            for exact_package, exact_transform in historical_packages_and_transforms:
                exact_key = (
                    exact_package.manifest_sha256,
                    deterministic_transform_contract_digest(exact_package),
                )
                if exact_key in exact_keys:
                    raise PackageContractError(
                        "duplicate immutable deterministic transform Package/contract"
                    )
                exact_keys.add(exact_key)
                historical_by_package.append(
                    HistoricalTransformResolution(
                        transform_id=transform_id,
                        package=exact_package,
                        contract_digest=exact_key[1],
                        transform=exact_transform,
                        contracts=ResolvedBlendContracts(
                            exact_transform.artifact.scientific_master,
                            historical_catalog,
                            historical_space,
                        ),
                    )
                )

        active_key = (
            transform.artifact.scientific_master.ref,
            commercial_catalog.ref,
            design_space.ref,
        )
        if active_key not in historical:
            raise PackageContractError(
                f"active resources for transform {transform_id} are not an available combination"
            )
        entries[transform_id] = LoadedTransform(
            transform_id=transform_id,
            package=package,
            transform=transform,
            commercial_catalog=commercial_catalog,
            design_space=design_space,
            package_locator=selection.active,
            available_package_locators=selection.available,
            commercial_catalog_locator=selection.commercial_catalog,
            design_space_locator=selection.design_space,
        )
    return DeterministicTransformCatalog(
        entries,
        historical,
        tuple(historical_by_package),
    )
