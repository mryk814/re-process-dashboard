"""Author a single-table repeated-measurement Observation Profile.

This seam is intentionally narrower than a join or ETL builder. It accepts one
CSV or one-sheet workbook, preserves observation and group identity, and emits
the existing Observation Profile consumed by Dataset registration and Package
verification.
"""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from decision_workbench.data.file_integrity import file_sha256
from decision_workbench.data.observation_profile import (
    CanonicalInput,
    CanonicalOutput,
    MetadataSource,
    ObservationAuthoringContract,
    ObservationDatasetProfile,
    ObservationEstimatorChoice,
    ObservationFamily,
    ObservationFeatureRecipe,
    ObservationValidationPlan,
    SingleTableObservationSource,
    build_observation_training_dataset,
)
from decision_workbench.data.profile_workbench import (
    validate_personal_profile_store_path,
)
from decision_workbench.modeling.model_lifecycle import dataset_profile_digest
from decision_workbench.task_composition.catalog import task_module
from decision_workbench.tasks.task_registry import load_task_contracts


def complex_data_authoring_capability() -> dict[str, object]:
    """Return the adopted complex-data authoring boundary for the Atlas."""

    return {
        "selected_family": {
            "family": "repeated_measurements",
            "status": "available",
            "profile_family": "observation-dataset-profile/v1",
            "source_contract": "single_visible_table",
            "validation_plan": "grouped_kfold",
            "feature_recipe": "observation-identity-v1",
            "estimator": "ridge.v1",
            "ui_entry": "profile_workbench",
        },
        "unselected_families": [
            {
                "family": "longitudinal_curve",
                "status": "specialized_only",
                "limitation": (
                    "axis, series-boundary, extrapolation, and Candidate "
                    "semantics remain Task-specific"
                ),
            },
            {
                "family": "relational_workbook",
                "status": "specialized_only",
                "limitation": (
                    "table keys, join direction, entity grain, and Candidate "
                    "authority remain Task-specific"
                ),
            },
        ],
        "scope": (
            "The reusable route authors new external single-table repeated "
            "observations; it does not rewrite bundled specialized Tasks."
        ),
    }


class AuthoringModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObservationInputBinding(AuthoringModel):
    path: str
    column: str
    source_unit: str | None = None


class ObservationTargetBinding(AuthoringModel):
    key: str
    column: str
    source_unit: str


class ObservationAuthoringRequest(AuthoringModel):
    task_id: str
    observation_grain: str = Field(min_length=3, max_length=160)
    observation_id_column: str
    group_column: str
    inputs: tuple[ObservationInputBinding, ...]
    targets: tuple[ObservationTargetBinding, ...]
    technical_metadata_columns: tuple[str, ...] = ()
    validation_folds: int = Field(default=5, ge=2, le=10)
    ridge_alpha: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def bindings_are_unique(self) -> "ObservationAuthoringRequest":
        for label, values in (
            ("input paths", [item.path for item in self.inputs]),
            ("input columns", [item.column for item in self.inputs]),
            ("target keys", [item.key for item in self.targets]),
            ("target columns", [item.column for item in self.targets]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        role_columns = [
            self.observation_id_column,
            self.group_column,
            *(item.column for item in self.inputs),
            *(item.column for item in self.targets),
            *self.technical_metadata_columns,
        ]
        if len(role_columns) != len(set(role_columns)):
            raise ValueError(
                "source columns must have exactly one authoring role"
            )
        return self


class ObservationAuthoringResult(AuthoringModel):
    profile_id: str
    profile_digest: str
    profile_locator: str
    source_sha256: str
    task_id: str
    observations: int
    eligible_observations: int
    groups: int
    quality_findings: tuple[str, ...]


class ObservationAuthoringField(AuthoringModel):
    key: str
    label: str
    kind: str
    unit: str | None = None


class ObservationAuthoringTask(AuthoringModel):
    task_id: str
    label: str
    inputs: tuple[ObservationAuthoringField, ...]
    targets: tuple[ObservationAuthoringField, ...]


_OBSERVATION_SOURCE_KINDS = {
    "welding_stage_c",
    "welding_graph_synthetic_demonstration",
}


def observation_authoring_tasks() -> tuple[ObservationAuthoringTask, ...]:
    contracts = load_task_contracts()
    result = []
    for task_id, contract in sorted(contracts.items()):
        module = task_module(task_id)
        if module.source_kind not in _OBSERVATION_SOURCE_KINDS:
            continue
        task = contract.task_definition
        result.append(ObservationAuthoringTask(
            task_id=task_id,
            label=task.label,
            inputs=tuple(
                ObservationAuthoringField(
                    key=field.path,
                    label=field.label,
                    kind=field.kind,
                    unit=field.unit,
                )
                for group in task.input_groups
                for field in group.fields
            ),
            targets=tuple(
                ObservationAuthoringField(
                    key=output.key,
                    label=output.label,
                    kind="number",
                    unit=output.unit,
                )
                for output in task.outputs
            ),
        ))
    return tuple(result)


def _profile_for(
    source: Path,
    request: ObservationAuthoringRequest,
) -> ObservationDatasetProfile:
    module = task_module(request.task_id)
    if module.source_kind not in _OBSERVATION_SOURCE_KINDS:
        raise ValueError(
            "選択したPrediction TaskはObservation family authoringに対応していません"
        )
    task = load_task_contracts()[request.task_id].task_definition
    fields = {
        field.path: field
        for group in task.input_groups
        for field in group.fields
    }
    outputs = {output.key: output for output in task.outputs}
    if set(item.path for item in request.inputs) != set(fields):
        raise ValueError("input bindings must match the TaskDefinition exactly")
    if set(item.key for item in request.targets) != set(outputs):
        raise ValueError("target bindings must match the TaskDefinition exactly")
    source_sha256 = file_sha256(source)
    identity_payload = json.dumps(
        {
            "source_sha256": source_sha256,
            "contract": request.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    identity = hashlib.sha256(identity_payload).hexdigest()[:16]
    task_slug = re.sub(r"[^A-Za-z0-9._-]", "-", request.task_id)
    profile_id = f"personal-observation-{task_slug}-{identity}"

    family_id = "authored-observations"
    inputs = []
    for binding in request.inputs:
        field = fields[binding.path]
        if field.kind == "heat_pattern":
            raise ValueError("Observation authoring does not accept heat_pattern inputs")
        if field.kind == "number":
            source_unit = binding.source_unit or field.unit
            if not source_unit or not field.unit:
                raise ValueError(f"numeric input requires units: {binding.path}")
            inputs.append(CanonicalInput(
                path=binding.path,
                role=family_id,
                column=binding.column,
                kind="numeric",
                source_unit=source_unit,
                canonical_unit=field.unit,
            ))
        else:
            if binding.source_unit is not None:
                raise ValueError(f"categorical input cannot declare a unit: {binding.path}")
            inputs.append(CanonicalInput(
                path=binding.path,
                role=family_id,
                column=binding.column,
                kind="categorical",
            ))
    targets = tuple(
        CanonicalOutput(
            key=binding.key,
            column=binding.column,
            source_unit=binding.source_unit,
            canonical_unit=outputs[binding.key].unit,
        )
        for binding in request.targets
    )
    return ObservationDatasetProfile(
        schema_version="observation-dataset-profile/v1",
        id=profile_id,
        task_id=request.task_id,
        relation_sheet="observations",
        entities=(),
        families=(ObservationFamily(
            id=family_id,
            sheet="observations",
            relation_column=request.observation_id_column,
            observation_id_column=request.observation_id_column,
            split_group_role="group",
            inputs=tuple(inputs),
            outputs=targets,
            metadata=tuple(
                MetadataSource(key=column, column=column)
                for column in request.technical_metadata_columns
            ),
        ),),
        single_table=SingleTableObservationSource(
            group_column=request.group_column,
        ),
        authoring=ObservationAuthoringContract(
            source_sha256=source_sha256,
            observation_grain=request.observation_grain,
            technical_metadata=request.technical_metadata_columns,
            validation_plan=ObservationValidationPlan(folds=request.validation_folds),
            feature_recipe=ObservationFeatureRecipe(),
            estimator=ObservationEstimatorChoice(alpha=request.ridge_alpha),
        ),
    )


def author_observation_profile(
    source: Path,
    request: ObservationAuthoringRequest,
    *,
    store_path: Path | None = None,
) -> ObservationAuthoringResult:
    source = source.resolve(strict=True)
    profile = _profile_for(source, request)
    training = build_observation_training_dataset(source, profile)
    view = next(iter(training.views.values()))
    eligible = [row for row in view.rows if row.eligible]
    groups = {row.split_group_key for row in eligible if row.split_group_key}
    if len(groups) < request.validation_folds:
        raise ValueError(
            f"grouped validation requires at least {request.validation_folds} eligible groups"
        )
    for target in view.summary.targets:
        if target.split_groups < request.validation_folds:
            raise ValueError(
                f"{target.target} grouped validation requires at least "
                f"{request.validation_folds} eligible groups; "
                f"found {target.split_groups}"
            )
    if len(eligible) <= len(groups):
        raise ValueError("repeated measurements require more observations than groups")

    digest = dataset_profile_digest(profile)
    store = validate_personal_profile_store_path(store_path)
    store.mkdir(parents=True, exist_ok=True)
    destination = store / f"{digest.removeprefix('sha256:')}.json"
    document = profile.model_dump(mode="json")
    payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if destination.exists() and destination.read_text(encoding="utf-8") != payload:
        raise FileExistsError("Profile digest collision")
    destination.write_text(payload, encoding="utf-8", newline="\n")
    findings = tuple(
        f"{target.target}: usable {target.usable_rows}/{view.summary.source_rows}"
        for target in view.summary.targets
        if target.usable_rows != view.summary.source_rows
    )
    return ObservationAuthoringResult(
        profile_id=profile.id,
        profile_digest=digest,
        profile_locator=str(destination),
        source_sha256=training.source_sha256,
        task_id=profile.task_id,
        observations=view.summary.source_rows,
        eligible_observations=view.summary.usable_input_rows,
        groups=len(groups),
        quality_findings=findings,
    )
