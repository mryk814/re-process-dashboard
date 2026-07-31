"""Allow-listed adapters for the supported Dataset Profile families.

Profile families deliberately have different schemas and runtime shapes.  This
module centralizes *selection* of those shapes without attempting to flatten
their contracts or dynamically load third-party implementations.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from material_workbench.contracts.task_contracts import TaskDefinition


class ProfileFamilyUnavailableError(ValueError):
    """Raised when a persisted Profile cannot be handled by an allow-listed family."""


class ProfileInspectionUnavailableError(ProfileFamilyUnavailableError):
    """Raised when a family has no training-inspection representation."""


@dataclass(frozen=True)
class ProfileRegistrationMetadata:
    profile_id: str
    task_ids: tuple[str, ...]
    effective_profile: dict[str, Any]


class ProfileFamilyAdapter(Protocol):
    schema_version: str

    def load_path(
        self,
        path: Path,
        task_definitions: Mapping[str, TaskDefinition] | None = None,
    ) -> Any: ...

    def restore(
        self,
        document: Mapping[str, Any],
        task_definitions: Mapping[str, TaskDefinition] | None = None,
    ) -> Any: ...

    def matches_stored_document(self, document: Mapping[str, Any]) -> bool: ...

    def matches_profile(self, profile: Any) -> bool: ...

    def registration_metadata(self, profile: Any) -> ProfileRegistrationMetadata: ...

    def validate_source(self, source: Path, profile_path: Path) -> dict[str, Any]: ...

    def load_training_descriptor(
        self, source: Path, profile: Any, task_id: str
    ) -> Any: ...

    def load_inspection_descriptor(self, source: Path, profile: Any) -> Any: ...


def _profile_digest(profile: Any) -> str:
    from material_workbench.modeling.model_lifecycle import dataset_profile_digest

    return dataset_profile_digest(profile)


def _generic_validation(source: Path, profile_path: Path, profile: Any) -> dict[str, Any]:
    """Validate a single-task non-workbook-canonical Profile through its Task."""

    from collections import Counter

    from material_workbench.task_composition.catalog import task_module

    metadata = profile_registration_metadata(profile)
    observations_by_task: dict[str, int] = {}
    all_observations: list[dict[str, Any]] = []
    for task_id in metadata.task_ids:
        data = task_module(task_id).data_loader(source, profile)
        eligible = [
            row
            for row in data.observations
            if row.get("eligible") and row.get("task_id", task_id) == task_id
        ]
        observations_by_task[task_id] = len(eligible)
        all_observations.extend(data.observations)
        if _profile_digest(lifecycle_profile_for_data(data)) != _profile_digest(profile):
            raise ValueError(
                f"loaded Profile identity differs from the selected Profile: {task_id}"
            )
    rejection_counts = Counter(
        reason
        for row in all_observations
        for reason in row.get("exclusion_reasons", ())
    )
    return {
        "ok": True,
        "registration_ready": any(observations_by_task.values()),
        "source": str(source),
        "source_sha256": file_sha256(source),
        "profile": str(profile_path),
        "profile_id": metadata.profile_id,
        "profile_digest": _profile_digest(profile),
        "task_ids": list(metadata.task_ids),
        "entities": len(all_observations),
        "relations": 0,
        "observations": len(all_observations),
        "observations_by_task": observations_by_task,
        "heat_series_parents": 0,
        "unresolved_heat_series_by_task": {},
        "rejected_by_policy": dict(sorted(rejection_counts.items())),
        "entity_preview": [
            {
                "entity_type": "observation",
                "entity_key": str(row.get("id", row.get("observation_id", index))),
                "values": {"eligible": bool(row.get("eligible"))},
            }
            for index, row in enumerate(all_observations[:5])
        ],
    }


@dataclass(frozen=True)
class DatasetInputProfileFamilyAdapter:
    schema_version: str = "dataset-input-profile/v2"

    def load_path(self, path: Path, task_definitions: Mapping[str, TaskDefinition] | None = None) -> Any:
        from material_workbench.data.profiles.loading import load_dataset_profile

        return load_dataset_profile(path, task_definitions)

    def restore(self, document: Mapping[str, Any], task_definitions: Mapping[str, TaskDefinition] | None = None) -> Any:
        from material_workbench.data.profiles.loading import load_task_definitions
        from material_workbench.data.profiles.schema import DatasetInputProfile
        from material_workbench.data.profiles.validation import validate_profile

        definitions = dict(task_definitions or load_task_definitions())
        raw_tasks = document.get("tasks")
        if not isinstance(raw_tasks, Mapping):
            raise ProfileFamilyUnavailableError("Dataset Input Profileのtasksを復元できません")
        selected = {
            task_id: definitions[task_id]
            for task_id in raw_tasks
            if task_id in definitions
        }
        profile = DatasetInputProfile.model_validate({
            **document,
            "task_definitions": selected,
        })
        validate_profile(profile, selected)
        return profile

    def matches_stored_document(self, document: Mapping[str, Any]) -> bool:
        return (
            "schema_version" not in document
            and isinstance(document.get("profile_id"), str)
            and isinstance(document.get("shared"), Mapping)
            and isinstance(document.get("tasks"), Mapping)
        )

    def matches_profile(self, profile: Any) -> bool:
        from material_workbench.data.profiles.schema import DatasetInputProfile

        return isinstance(profile, DatasetInputProfile)

    def registration_metadata(self, profile: Any) -> ProfileRegistrationMetadata:
        return ProfileRegistrationMetadata(
            profile_id=str(profile.profile_id),
            task_ids=tuple(sorted(profile.tasks)),
            effective_profile=profile.model_dump(mode="json", exclude={"task_definitions"}),
        )

    def validate_source(self, source: Path, profile_path: Path) -> dict[str, Any]:
        from material_workbench.data.profile_workbench import _validate_dataset_input_workbook

        return _validate_dataset_input_workbook(source, profile_path)

    def load_training_descriptor(self, source: Path, profile: Any, task_id: str) -> Any:
        if task_id not in profile.tasks:
            raise ProfileFamilyUnavailableError("Profile RevisionはこのPrediction Taskに対応していません")
        from material_workbench.task_composition.catalog import task_module

        return task_module(task_id).data_loader(source, profile)

    def load_inspection_descriptor(self, source: Path, profile: Any) -> Any:
        raise ProfileInspectionUnavailableError("Dataset Input Profileには観測学習Inspectorがありません")


@dataclass(frozen=True)
class TabularProfileFamilyAdapter:
    schema_version: str = "tabular-dataset-profile/v1"

    def load_path(self, path: Path, task_definitions: Mapping[str, TaskDefinition] | None = None) -> Any:
        del task_definitions
        from material_workbench.modeling.tabular_regression import load_tabular_profile

        return load_tabular_profile(path)

    def restore(self, document: Mapping[str, Any], task_definitions: Mapping[str, TaskDefinition] | None = None) -> Any:
        del task_definitions
        from material_workbench.modeling.tabular_regression import TabularDatasetProfile

        return TabularDatasetProfile.model_validate(document)

    def matches_stored_document(self, document: Mapping[str, Any]) -> bool:
        return False

    def matches_profile(self, profile: Any) -> bool:
        from material_workbench.modeling.tabular_regression import TabularDatasetProfile

        return isinstance(profile, TabularDatasetProfile)

    def registration_metadata(self, profile: Any) -> ProfileRegistrationMetadata:
        return ProfileRegistrationMetadata(
            profile_id=str(profile.profile_id),
            task_ids=(str(profile.task_id),),
            effective_profile=profile.model_dump(mode="json", exclude={"task_definitions"}),
        )

    def validate_source(self, source: Path, profile_path: Path) -> dict[str, Any]:
        return _generic_validation(source, profile_path, self.load_path(profile_path))

    def load_training_descriptor(self, source: Path, profile: Any, task_id: str) -> Any:
        if task_id != profile.task_id:
            raise ProfileFamilyUnavailableError("Profile RevisionはこのPrediction Taskに対応していません")
        from material_workbench.task_composition.catalog import task_module

        return task_module(task_id).data_loader(source, profile)

    def load_inspection_descriptor(self, source: Path, profile: Any) -> Any:
        raise ProfileInspectionUnavailableError("Tabular Dataset Profileには観測学習Inspectorがありません")


@dataclass(frozen=True)
class ObservationProfileFamilyAdapter:
    schema_version: str = "observation-dataset-profile/v1"

    def load_path(self, path: Path, task_definitions: Mapping[str, TaskDefinition] | None = None) -> Any:
        del task_definitions
        from material_workbench.data.observation_profile import load_observation_profile

        return load_observation_profile(path)

    def restore(self, document: Mapping[str, Any], task_definitions: Mapping[str, TaskDefinition] | None = None) -> Any:
        del task_definitions
        from material_workbench.data.observation_profile import ObservationDatasetProfile

        return ObservationDatasetProfile.model_validate(document)

    def matches_stored_document(self, document: Mapping[str, Any]) -> bool:
        return False

    def matches_profile(self, profile: Any) -> bool:
        from material_workbench.data.observation_profile import ObservationDatasetProfile

        return isinstance(profile, ObservationDatasetProfile)

    def registration_metadata(self, profile: Any) -> ProfileRegistrationMetadata:
        return ProfileRegistrationMetadata(
            profile_id=str(profile.profile_id),
            task_ids=(str(profile.task_id),),
            effective_profile=profile.model_dump(mode="json", exclude={"task_definitions"}),
        )

    def validate_source(self, source: Path, profile_path: Path) -> dict[str, Any]:
        return _generic_validation(source, profile_path, self.load_path(profile_path))

    def load_training_descriptor(self, source: Path, profile: Any, task_id: str) -> Any:
        if task_id != profile.task_id:
            raise ProfileFamilyUnavailableError("Profile RevisionはこのPrediction Taskに対応していません")
        from material_workbench.task_composition.catalog import task_module

        return task_module(task_id).data_loader(source, profile)

    def load_inspection_descriptor(self, source: Path, profile: Any) -> Any:
        from material_workbench.data.observation_profile import build_observation_training_dataset

        return build_observation_training_dataset(source, profile)


@dataclass(frozen=True)
class StageBProfileFamilyAdapter:
    schema_version: str = "welding-stage-b-profile/v1"

    def load_path(self, path: Path, task_definitions: Mapping[str, TaskDefinition] | None = None) -> Any:
        del task_definitions
        from material_workbench.data.stage_b_training import load_stage_b_profile

        return load_stage_b_profile(path)

    def restore(self, document: Mapping[str, Any], task_definitions: Mapping[str, TaskDefinition] | None = None) -> Any:
        del task_definitions
        from material_workbench.data.stage_b_training import StageBWorkbookProfile

        return StageBWorkbookProfile.model_validate(document)

    def matches_stored_document(self, document: Mapping[str, Any]) -> bool:
        return False

    def matches_profile(self, profile: Any) -> bool:
        from material_workbench.data.stage_b_training import StageBWorkbookProfile

        return isinstance(profile, StageBWorkbookProfile)

    def registration_metadata(self, profile: Any) -> ProfileRegistrationMetadata:
        return ProfileRegistrationMetadata(
            profile_id=str(profile.id),
            task_ids=(str(profile.task_id),),
            effective_profile=profile.model_dump(mode="json", exclude={"task_definitions"}),
        )

    def validate_source(self, source: Path, profile_path: Path) -> dict[str, Any]:
        from material_workbench.data.profile_workbench import _stage_b_validation

        return _stage_b_validation(source, self.load_path(profile_path), profile_path)

    def load_training_descriptor(self, source: Path, profile: Any, task_id: str) -> Any:
        if task_id != profile.task_id:
            raise ProfileFamilyUnavailableError("Profile RevisionはこのPrediction Taskに対応していません")
        from material_workbench.task_composition.catalog import task_module

        return task_module(task_id).data_loader(source, profile)

    def load_inspection_descriptor(self, source: Path, profile: Any) -> Any:
        from material_workbench.data.stage_b_training import (
            build_stage_b_training_data,
            stage_b_inspection_dataset,
        )

        return stage_b_inspection_dataset(build_stage_b_training_data(source, profile))


class ProfileFamilyAdapterRegistry:
    """Fixed registry for the four shipped Dataset Profile families."""

    def __init__(self, adapters: tuple[ProfileFamilyAdapter, ...]) -> None:
        versions = [adapter.schema_version for adapter in adapters]
        if len(versions) != len(set(versions)):
            raise ValueError("Profile family schema_version must be unique")
        self._by_schema = {adapter.schema_version: adapter for adapter in adapters}

    def resolve_schema(self, schema_version: object) -> ProfileFamilyAdapter:
        if not isinstance(schema_version, str) or not schema_version:
            raise ProfileFamilyUnavailableError("Profile schema_versionがありません")
        try:
            return self._by_schema[schema_version]
        except KeyError as exc:
            raise ProfileFamilyUnavailableError(
                f"未対応のProfile schema_versionです: {schema_version!r}"
            ) from exc

    def resolve_document(self, document: Mapping[str, Any]) -> ProfileFamilyAdapter:
        return self.resolve_schema(document.get("schema_version"))

    def resolve_stored_document(self, document: Mapping[str, Any]) -> ProfileFamilyAdapter:
        schema_version = document.get("schema_version")
        if schema_version is not None:
            return self.resolve_schema(schema_version)
        matches = [
            adapter for adapter in self._by_schema.values()
            if adapter.matches_stored_document(document)
        ]
        if len(matches) == 1:
            return matches[0]
        raise ProfileFamilyUnavailableError("保存済みProfileのfamilyを判定できません")

    def resolve_profile(self, profile: Any) -> ProfileFamilyAdapter:
        matches = [
            adapter for adapter in self._by_schema.values()
            if adapter.matches_profile(profile)
        ]
        if len(matches) == 1:
            return matches[0]
        raise ProfileFamilyUnavailableError("Profile objectのfamilyを判定できません")


PROFILE_FAMILY_REGISTRY = ProfileFamilyAdapterRegistry((
    DatasetInputProfileFamilyAdapter(),
    TabularProfileFamilyAdapter(),
    ObservationProfileFamilyAdapter(),
    StageBProfileFamilyAdapter(),
))


def _read_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileFamilyUnavailableError(f"Profileを読み取れません: {path}") from exc
    if not isinstance(document, dict):
        raise ProfileFamilyUnavailableError("Profile documentはobjectでなければなりません")
    return document


def load_profile_document(
    path: str | Path,
    task_definitions: Mapping[str, TaskDefinition] | None = None,
) -> Any:
    profile_path = Path(path).resolve(strict=True)
    return PROFILE_FAMILY_REGISTRY.resolve_document(_read_document(profile_path)).load_path(
        profile_path, task_definitions
    )


def restore_profile_document(
    document: Mapping[str, Any],
    task_definitions: Mapping[str, TaskDefinition] | None = None,
) -> Any:
    return PROFILE_FAMILY_REGISTRY.resolve_stored_document(document).restore(
        document, task_definitions
    )


def profile_registration_metadata(profile: Any) -> ProfileRegistrationMetadata:
    return PROFILE_FAMILY_REGISTRY.resolve_profile(profile).registration_metadata(profile)


def supported_task_ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    adapter = PROFILE_FAMILY_REGISTRY.resolve_stored_document(document)
    if adapter.schema_version == "dataset-input-profile/v2":
        tasks = document.get("tasks")
        return tuple(sorted(tasks)) if isinstance(tasks, Mapping) else ()
    task_id = document.get("task_id")
    return (task_id,) if isinstance(task_id, str) and task_id else ()


def profile_task_ids(profile: Any) -> tuple[str, ...]:
    return profile_registration_metadata(profile).task_ids


def validate_profile_source(source: Path, profile_path: Path) -> dict[str, Any]:
    document = _read_document(profile_path)
    return PROFILE_FAMILY_REGISTRY.resolve_document(document).validate_source(source, profile_path)


def load_training_descriptor(source: Path, profile: Any, task_id: str) -> Any:
    adapter = PROFILE_FAMILY_REGISTRY.resolve_profile(profile)
    return adapter.load_training_descriptor(source, profile, task_id)


def load_inspection_descriptor(source: Path, profile_path: Path) -> Any:
    profile = load_profile_document(profile_path)
    adapter = PROFILE_FAMILY_REGISTRY.resolve_document(_read_document(profile_path))
    return adapter.load_inspection_descriptor(source, profile)


def lifecycle_profile_for_data(data: Any) -> Any:
    lifecycle_profile = getattr(data, "lifecycle_profile", None)
    if lifecycle_profile is not None:
        return lifecycle_profile
    profile = getattr(data, "profile", None)
    if profile is not None:
        return profile
    return Path(data.profile_path)


def file_sha256(path: Path) -> str:
    from material_workbench.data.file_integrity import file_sha256 as _file_sha256

    return _file_sha256(path)
