"""Profile-driven CSV regression tasks used by bundled external datasets.

The source CSV remains an immutable input.  A small JSON profile owns column
meaning, candidate paths, grouping, targets, and categorical levels; Python
code contains no dataset-specific column names.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from material_workbench.contracts.feature_contracts import FeatureBundle, FeatureDefinition
from material_workbench.contracts.schemas import Candidate, CandidateInput, Prediction, Support, TargetValue
from material_workbench.data.dataset_profile import load_task_definitions
from material_workbench.domain.goal_targets import goal_fields
from material_workbench.modeling.curve_grid import anchored_curve_grid
from material_workbench.modeling.model_packages import (
    ModelPackageLoader,
    VerifiedModelPackage,
    predictive_interval,
    validate_predictive_summary,
    validate_task_definition_canonical_inputs,
)
from material_workbench.tasks.task_registry import load_task_contracts


class TabularInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    column: str
    kind: Literal["number", "categorical"]
    unit: str = ""
    choices: tuple[str, ...] = ()
    transform: Literal["quadratic", "linear", "log1p"] = "quadratic"
    interact_with_axis: bool = False
    main_effect: bool = True

    @model_validator(mode="after")
    def categorical_shape(self) -> "TabularInput":
        if (self.kind == "categorical") != bool(self.choices):
            raise ValueError("categorical inputs require choices; numeric inputs must omit them")
        if self.kind == "categorical" and self.transform != "quadratic":
            raise ValueError("categorical inputs cannot declare a numeric transform")
        return self


class TabularOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str
    column: str
    unit: str
    lower_bound: float | None = None
    upper_bound: float | None = None

    @model_validator(mode="after")
    def ordered_bounds(self) -> "TabularOutput":
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.lower_bound > self.upper_bound
        ):
            raise ValueError("output lower_bound must not exceed upper_bound")
        return self


class TabularQualityRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    column: str
    label: str
    kind: Literal["below_minimum", "repeated_value_fraction"]
    minimum: float | None = None
    value: float | None = None
    fraction: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def rule_parameters_match_kind(self) -> "TabularQualityRule":
        if self.kind == "below_minimum":
            if self.minimum is None or self.value is not None or self.fraction is not None:
                raise ValueError("below_minimum requires only minimum")
        elif self.value is None or self.fraction is None or self.minimum is not None:
            raise ValueError("repeated_value_fraction requires value and fraction")
        return self


class TabularDatasetProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["tabular-dataset-profile/v1"]
    profile_id: str
    name: str
    task_id: str
    package_id: str
    id_column: str | None = None
    group_column: str | None = None
    curve_axis_path: str | None = None
    interaction_axis_path: str | None = None
    model_family: Literal["ridge", "lightgbm_monotone"] = "ridge"
    monotone_decreasing_paths: tuple[str, ...] = ()
    inputs: tuple[TabularInput, ...] = Field(min_length=1)
    outputs: tuple[TabularOutput, ...] = Field(min_length=1)
    quality_rules: tuple[TabularQualityRule, ...] = ()

    @model_validator(mode="after")
    def unique_contract(self) -> "TabularDatasetProfile":
        paths = [item.path for item in self.inputs]
        columns = [item.column for item in self.inputs]
        targets = [item.key for item in self.outputs]
        if len(paths) != len(set(paths)) or len(columns) != len(set(columns)):
            raise ValueError("tabular input paths and columns must be unique")
        if len(targets) != len(set(targets)):
            raise ValueError("tabular output keys must be unique")
        if self.interaction_axis_path is not None:
            axis = next(
                (item for item in self.inputs if item.path == self.interaction_axis_path),
                None,
            )
            if axis is None or axis.kind != "number":
                raise ValueError("interaction_axis_path must identify a numeric input")
            if axis.interact_with_axis:
                raise ValueError("interaction axis cannot interact with itself")
        elif any(item.interact_with_axis for item in self.inputs):
            raise ValueError("interact_with_axis requires interaction_axis_path")
        if any(not item.main_effect and not item.interact_with_axis for item in self.inputs):
            raise ValueError("inputs without a main effect must interact with the axis")
        numeric_paths = {item.path for item in self.inputs if item.kind == "number"}
        if not set(self.monotone_decreasing_paths) <= numeric_paths:
            raise ValueError("monotone paths must identify numeric inputs")
        if (self.model_family == "lightgbm_monotone") != bool(self.monotone_decreasing_paths):
            raise ValueError("lightgbm_monotone requires monotone_decreasing_paths")
        return self


def load_tabular_profile(path: str | Path) -> TabularDatasetProfile:
    return TabularDatasetProfile.model_validate_json(Path(path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class TabularData:
    source_path: str
    source_mtime_ns: int
    source_sha256: str
    profile_path: str
    profile: TabularDatasetProfile
    profile_id: str
    observations: list[dict[str, Any]]
    medians: dict[str, float]
    measurement_labels: dict[str, str]
    row_count: int
    quality: list[dict[str, Any]]
    detected_quality: list[dict[str, Any]]
    technical_columns: dict[tuple[str, str], str]


def _get_path(candidate: CandidateInput, path: str) -> float | str:
    group, key = path.split(".", 1)
    values = getattr(candidate.inputs, group)
    return values[key]


def _set_path(candidate: CandidateInput, path: str, value: float) -> None:
    group, key = path.split(".", 1)
    getattr(candidate.inputs, group)[key] = float(value)


def feature_definitions(profile: TabularDatasetProfile) -> tuple[FeatureDefinition, ...]:
    definitions: list[FeatureDefinition] = []
    for item in profile.inputs:
        if not item.main_effect:
            continue
        group = item.path.split(".", 1)[0]
        feature_group = group if group in {"composition", "process", "categorical"} else "other"
        if item.kind == "number":
            if item.transform == "log1p":
                definitions.append(
                    FeatureDefinition(
                        f"{item.path}__log1p",
                        "1",
                        f"log1p transformed {item.path}",
                        feature_group,
                    )
                )
            elif item.transform == "quadratic":
                definitions.extend((
                    FeatureDefinition(item.path, item.unit, f"{item.path} raw value", feature_group),
                    FeatureDefinition(f"{item.path}__square", f"{item.unit}^2", f"{item.path} quadratic term", feature_group),
                ))
            else:
                definitions.append(
                    FeatureDefinition(item.path, item.unit, f"{item.path} raw value", feature_group)
                )
        else:
            definitions.extend(
                FeatureDefinition(
                    f"{item.path}__{choice}",
                    "1",
                    f"{item.path} is {choice}",
                    "categorical",
                )
                for choice in item.choices
            )
    if profile.interaction_axis_path is not None:
        axis = next(item for item in profile.inputs if item.path == profile.interaction_axis_path)
        for item in profile.inputs:
            if not item.interact_with_axis:
                continue
            group = item.path.split(".", 1)[0]
            feature_group = group if group in {"composition", "process", "categorical"} else "other"
            if item.kind == "number":
                definitions.append(FeatureDefinition(
                    f"{axis.path}__x__{item.path}",
                    f"{axis.unit}*{item.unit}",
                    f"{axis.path} interaction with {item.path}",
                    feature_group,
                ))
            else:
                definitions.extend(
                    FeatureDefinition(
                        f"{axis.path}__x__{item.path}__{choice}",
                        axis.unit,
                        f"{axis.path} interaction with {item.path} is {choice}",
                        "categorical",
                    )
                    for choice in item.choices
                )
    return tuple(definitions)


def build_tabular_features(
    candidate: CandidateInput,
    profile: TabularDatasetProfile,
) -> FeatureBundle:
    values: list[float] = []
    for item in profile.inputs:
        value = _get_path(candidate, item.path)
        if not item.main_effect:
            continue
        if item.kind == "number":
            numeric = float(value)
            if item.transform == "log1p":
                if numeric < 0:
                    raise ValueError(f"{item.path} must be non-negative for log1p transform")
                values.append(math.log1p(numeric))
            elif item.transform == "quadratic":
                values.extend((numeric, numeric * numeric))
            else:
                values.append(numeric)
        else:
            values.extend(float(value == choice) for choice in item.choices)
    if profile.interaction_axis_path is not None:
        axis_value = float(_get_path(candidate, profile.interaction_axis_path))
        for item in profile.inputs:
            if not item.interact_with_axis:
                continue
            value = _get_path(candidate, item.path)
            if item.kind == "number":
                values.append(axis_value * float(value))
            else:
                values.extend(axis_value * float(value == choice) for choice in item.choices)
    return FeatureBundle(
        pipeline_id=f"{profile.task_id}-profile-transform",
        pipeline_version="1.0.0",
        definitions=feature_definitions(profile),
        values=np.asarray(values, dtype=float),
    )


def candidate_from_observation(row: dict[str, Any], profile: TabularDatasetProfile) -> CandidateInput:
    return CandidateInput(
        name=str(row["id"]),
        inputs={
            "composition": dict(row["composition"] or {}),
            "process": dict(row["features"] or {}),
            "categorical": dict(row["categorical"] or {}),
            "heat_pattern": None,
        },
    )


def build_tabular_features_from_observation(
    row: dict[str, Any],
    _medians: dict[str, float],
    profile: TabularDatasetProfile,
) -> FeatureBundle:
    return build_tabular_features(candidate_from_observation(row, profile), profile)


def load_tabular_data(
    path: str | Path,
    profile_path: str | Path | TabularDatasetProfile,
) -> TabularData:
    source = Path(path)
    if isinstance(profile_path, TabularDatasetProfile):
        profile = profile_path
        profile_locator = f"catalog:{profile.profile_id}"
    else:
        profile_file = Path(profile_path)
        profile = load_tabular_profile(profile_file)
        profile_locator = str(profile_file)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    observations: list[dict[str, Any]] = []
    numeric_series: dict[str, list[float]] = {
        item.path: [] for item in profile.inputs if item.kind == "number"
    }
    quality_values: dict[str, list[float]] = {
        rule.column: [] for rule in profile.quality_rules
    }
    quality_records: dict[str, list[tuple[str, str, float]]] = {
        rule.column: [] for rule in profile.quality_rules
    }
    curve_axis_column = next(
        (
            item.column
            for item in profile.inputs
            if item.path == profile.curve_axis_path
        ),
        None,
    )
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {
            *(item.column for item in profile.inputs),
            *(item.column for item in profile.outputs),
            *(rule.column for rule in profile.quality_rules),
        }
        required.update(
            column for column in (profile.id_column, profile.group_column) if column is not None
        )
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise ValueError(f"CSVにProfile必須列がありません: {', '.join(missing)}")
        for index, raw in enumerate(reader, start=2):
            reasons: list[str] = []
            composition: dict[str, float] = {}
            process: dict[str, float] = {}
            categorical: dict[str, str] = {}
            for item in profile.inputs:
                text = (raw.get(item.column) or "").strip()
                group, key = item.path.split(".", 1)
                if item.kind == "number":
                    try:
                        value = float(text)
                        if not math.isfinite(value):
                            raise ValueError
                    except ValueError:
                        reasons.append(f"{item.column}が有限数ではありません")
                        continue
                    (composition if group == "composition" else process)[key] = value
                    numeric_series[item.path].append(value)
                elif text not in item.choices:
                    reasons.append(f"{item.column}が定義済み区分ではありません")
                else:
                    categorical[key] = text
            outputs: dict[str, float] = {}
            for item in profile.outputs:
                try:
                    value = float((raw.get(item.column) or "").strip())
                    if not math.isfinite(value):
                        raise ValueError
                    outputs[item.key] = value
                except ValueError:
                    reasons.append(f"{item.column}が有限数ではありません")
            for column, values in quality_values.items():
                try:
                    value = float((raw.get(column) or "").strip())
                    if math.isfinite(value):
                        values.append(value)
                except ValueError:
                    continue
            observation_id = (
                (raw.get(profile.id_column) or "").strip()
                if profile.id_column is not None
                else ""
            ) or f"row-{index}"
            group_id = (
                (raw.get(profile.group_column) or "").strip()
                if profile.group_column is not None
                else ""
            ) or observation_id
            for column in quality_records:
                try:
                    quality_value = float((raw.get(column) or "").strip())
                except ValueError:
                    continue
                axis_value = (raw.get(curve_axis_column) or "").strip() if curve_axis_column else ""
                quality_records[column].append((group_id, axis_value, quality_value))
            observations.append({
                "id": observation_id if observation_id != group_id else f"{observation_id}:{index}",
                "task_id": profile.task_id,
                "source": source.name,
                "parent_key": group_id,
                "features": process,
                "composition": composition,
                "categorical": categorical,
                "outputs": outputs,
                "eligible": not reasons,
                "eligibility_reasons": reasons,
                "date": None,
                "measurement_order": index,
                "run_context": {"group_key": group_id},
            })
    medians = {
        path.split(".", 1)[1]: float(median(values))
        for path, values in numeric_series.items()
        if path.startswith("composition.") and values
    }
    detected_quality: list[dict[str, Any]] = []
    for rule in profile.quality_rules:
        values = quality_values[rule.column]
        if not values:
            continue
        if rule.kind == "below_minimum":
            assert rule.minimum is not None
            affected = [value for value in values if value < rule.minimum]
            if not affected:
                continue
            affected_records = [
                record for record in quality_records[rule.column]
                if record[2] < rule.minimum
            ]
            examples = ", ".join(
                f"{group}@{axis or 'row'}={value:g}"
                for group, axis, value in affected_records[:3]
            )
            detail = (
                f"{rule.label}が下限{rule.minimum:g}未満の行が"
                f"{len(affected):,}/{len(values):,}件あります（最小{min(affected):g}）。"
                f"影響する親条件は{len({record[0] for record in affected_records}):,}件です。"
                f"例: {examples}。"
                "学習からは自動除外していません。"
            )
            issue_type = "out_of_range"
        else:
            assert rule.value is not None and rule.fraction is not None
            affected = [value for value in values if value == rule.value]
            share = len(affected) / len(values)
            if share < rule.fraction:
                continue
            affected_records = [
                record for record in quality_records[rule.column]
                if record[2] == rule.value
            ]
            examples = ", ".join(
                f"{group}@{axis or 'row'}={value:g}"
                for group, axis, value in affected_records[:3]
            )
            detail = (
                f"{rule.label}が{rule.value:g}に一致する行が"
                f"{len(affected):,}/{len(values):,}件（{share:.1%}）あります。"
                f"影響する親条件は{len({record[0] for record in affected_records}):,}件です。"
                f"例: {examples}。"
                "打ち切り・クリップ・測定限界の可能性を確認してください。"
                "学習からは自動除外していません。"
            )
            issue_type = "suspicious_distribution"
        detected_quality.append({
            "issue_id": f"tabular:{profile.task_id}:{rule.id}",
            "issue_type": issue_type,
            "source_sheet": source.name,
            "entity_key": rule.column,
            "detail": detail,
            "focus_entity_key": None,
            "related_entity_keys": [],
            "missing_reference_key": None,
            "suggested_view": "source_sheet",
        })
    return TabularData(
        source_path=str(source),
        source_mtime_ns=source.stat().st_mtime_ns,
        source_sha256=digest,
        profile_path=profile_locator,
        profile=profile,
        profile_id=profile.profile_id,
        observations=observations,
        medians=medians,
        measurement_labels={item.key: item.key for item in profile.outputs},
        row_count=len(observations),
        quality=[],
        detected_quality=detected_quality,
        technical_columns={},
    )


class TabularRegressionRuntime:
    support_policy_id = "tabular-row-knn-v1"

    def __init__(self, data: TabularData, package_root: str | Path) -> None:
        self.data = data
        self.profile = data.profile
        self.task_id = data.profile.task_id
        self.model_package: VerifiedModelPackage = ModelPackageLoader().load(package_root)
        manifest = self.model_package.manifest
        definition = load_task_definitions()[self.task_id]
        validate_task_definition_canonical_inputs(definition, manifest)
        if manifest.task_id != self.task_id:
            raise ValueError(f"Model package task {manifest.task_id} is incompatible with {self.task_id}")
        expected_features = tuple(item.name for item in feature_definitions(self.profile))
        if tuple(manifest.feature_pipeline.output_features) != expected_features:
            raise ValueError("Tabular model package feature order is incompatible")
        self.predictors = {
            spec.target: self.model_package.load_predictor(spec.id)
            for spec in manifest.predictors
        }
        stats_path = next(path for path in manifest.feature_pipeline.artifacts if path.endswith("training_stats.json"))
        self.training_stats = json.loads(
            self.model_package.artifact_path(stats_path).read_text(encoding="utf-8")
        )
        self._verify_smoke()
        self._build_support_reference()

    @property
    def output_keys(self) -> frozenset[str]:
        return frozenset(self.predictors)

    def _verify_smoke(self) -> None:
        smoke = self.model_package.manifest.smoke_test
        if smoke is None:
            raise ValueError("Tabular model package must declare a smoke test")
        candidate = CandidateInput.model_validate_json(
            self.model_package.artifact_path(smoke.input).read_text(encoding="utf-8")
        )
        expected = json.loads(
            self.model_package.artifact_path(smoke.expected).read_text(encoding="utf-8")
        )
        values = build_tabular_features(candidate, self.profile).as_dict()
        specs = {item.target: item for item in self.model_package.manifest.predictors}
        capabilities = {
            item.target: item for item in load_task_contracts()[self.task_id].runtime_capability.targets
        }
        for target, predictor in self.predictors.items():
            summary = predictor.predict(values)
            validate_predictive_summary(summary, specs[target], capabilities[target])
            if not np.isclose(summary.point_estimate, expected[target], rtol=1e-7, atol=1e-7):
                raise ValueError("Tabular model package smoke prediction is not reproducible")

    def _build_support_reference(self) -> None:
        eligible = [row for row in self.data.observations if row["eligible"]]
        self.reference_rows = eligible
        raw = np.vstack([
            build_tabular_features_from_observation(row, self.data.medians, self.profile).values
            for row in eligible
        ])
        self.reference_mean = raw.mean(axis=0)
        self.reference_scale = raw.std(axis=0)
        self.reference_scale[self.reference_scale < 1e-9] = 1.0
        self.reference_vectors = (raw - self.reference_mean) / self.reference_scale
        if len(raw) > 1:
            sample = self.reference_vectors
            sample_groups = np.asarray([str(row["parent_key"]) for row in eligible])
            # Support calibration is only a robust distance scale estimate.
            # A deterministic 500-row sample avoids an O(n²d) startup allocation
            # (the wear example has more than 14k reference observations).
            if len(sample) > 500:
                sample_indexes = np.linspace(0, len(sample) - 1, 500, dtype=int)
                sample = sample[sample_indexes]
                sample_groups = sample_groups[sample_indexes]
            distances = np.sqrt(((sample[:, None, :] - sample[None, :, :]) ** 2).mean(axis=2))
            distances[sample_groups[:, None] == sample_groups[None, :]] = np.inf
            self.loo_nearest = distances.min(axis=1)
        else:
            self.loo_nearest = np.asarray([0.0])

    def _support(self, candidate: CandidateInput, include_similarity: bool) -> tuple[Support, list[dict[str, Any]]]:
        vector = build_tabular_features(candidate, self.profile).values
        normalized = (vector - self.reference_mean) / self.reference_scale
        distances = np.sqrt(((self.reference_vectors - normalized) ** 2).mean(axis=1))
        nearest = float(distances.min())
        supported, caution = (float(value) for value in np.quantile(self.loo_nearest, (0.80, 0.95)))
        if nearest <= supported:
            status, message = "supported", "近い学習条件に実測があります"
        elif nearest <= caution:
            status, message = "caution", "近傍はありますが、学習条件の密度が低い領域です"
        else:
            status, message = "extrapolated", "学習条件から外れています。予測は探索的な参考です"
        similar: list[dict[str, Any]] = []
        if include_similarity:
            used_groups: set[str] = set()
            for index in np.argsort(distances):
                row = self.reference_rows[int(index)]
                parent_key = str(row["parent_key"])
                if self.profile.group_column and parent_key in used_groups:
                    continue
                used_groups.add(parent_key)
                similar.append({
                    "observation_id": row["id"],
                    "observation_ids": [row["id"]],
                    "parent_key": row["parent_key"],
                    "source": self.data.profile.name,
                    "distance": round(float(distances[index]), 4),
                    "outputs": {key: round(float(value), 4) for key, value in row["outputs"].items()},
                })
                if len(similar) == 6:
                    break
        return Support(
            status=status,
            distance=round(nearest, 4),
            percentile=round(float((self.loo_nearest <= nearest).mean() * 100), 1),
            message=message,
            components={"all_inputs": round(nearest, 4)},
            reference_count=len(self.reference_rows),
            supported_threshold=round(supported, 4),
            caution_threshold=round(caution, 4),
        ), similar

    def evidence(self, candidate: Candidate) -> tuple[Support, list[dict[str, Any]]]:
        return self._support(candidate, True)

    def support_summary(self, candidate: Candidate) -> Support:
        return self._support(candidate, False)[0]

    def support_by_target(self, candidate: Candidate) -> dict[str, Support]:
        support = self.support_summary(candidate)
        return {target: support for target in self.output_keys}

    def similarity(self, candidate: Candidate, limit: int = 6) -> list[dict[str, Any]]:
        return self.evidence(candidate)[1][:limit]

    def predict_core(
        self,
        candidate: Candidate,
        detailed: bool = False,
        target_values: dict[str, TargetValue] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        values = build_tabular_features(candidate, self.profile).as_dict()
        definitions = {item.key: item for item in load_task_definitions()[self.task_id].outputs}
        predictions: dict[str, Prediction] = {}
        warnings: list[str] = []
        for target, predictor in self.predictors.items():
            summary = predictor.predict(values)
            lower, upper = predictive_interval(summary)
            output_profile = next(item for item in self.profile.outputs if item.key == target)
            point_estimate = summary.point_estimate
            quantiles = {
                level: float(value)
                for level, value in summary.quantiles.items()
            }
            if output_profile.lower_bound is not None:
                point_estimate = max(output_profile.lower_bound, point_estimate)
                lower = max(output_profile.lower_bound, lower)
                upper = max(output_profile.lower_bound, upper)
                quantiles = {
                    level: max(output_profile.lower_bound, value)
                    for level, value in quantiles.items()
                }
            if output_profile.upper_bound is not None:
                point_estimate = min(output_profile.upper_bound, point_estimate)
                lower = min(output_profile.upper_bound, lower)
                upper = min(output_profile.upper_bound, upper)
                quantiles = {
                    level: min(output_profile.upper_bound, value)
                    for level, value in quantiles.items()
                }
            goal = (target_values or {}).get(target)
            goal_probability = None
            goal_value, goal_lower, goal_upper, goal_direction = goal_fields(
                goal, definitions[target].goal_direction
            )
            predictions[target] = Prediction(
                value=round(point_estimate, 4),
                lower=round(lower, 4),
                upper=round(upper, 4),
                unit=summary.unit,
                target_kind=summary.target_kind,
                point_statistic=summary.point_statistic,
                predictive_family=summary.distribution["family"],
                quantiles={level: round(value, 6) for level, value in quantiles.items()},
                goal_value=goal_value,
                goal_lower=goal_lower,
                goal_upper=goal_upper,
                goal_probability=None if goal_probability is None else round(goal_probability, 4),
                goal_direction=goal_direction,
            )
            warnings.extend(summary.warnings)
        runtime_types = sorted({
            item.runtime_type for item in self.model_package.manifest.predictors
        })
        uses_monotone_lightgbm = runtime_types == ["lightgbm.booster.v1"]
        return {
            "task_id": self.task_id,
            "candidate_id": candidate.id,
            "mode": "detailed" if detailed else "preview",
            "predictions": predictions,
            "warnings": warnings,
            "canonical_input": {
                "input_schema_version": "canonical-candidate/v1",
                "composition": candidate.inputs.composition,
                "process": candidate.inputs.process,
                "categorical": candidate.inputs.categorical,
                "feature_vector": values,
            },
            "model_meta": {
                "task_id": self.task_id,
                "model": {
                    "id": self.model_package.manifest.package_id,
                    "version": self.model_package.manifest.package_version,
                    "method": (
                        "monotonic gradient-boosted trees with grouped validation"
                        if uses_monotone_lightgbm
                        else (
                            "regularized regression with grouped validation"
                            if self.profile.group_column
                            else "regularized regression with row-wise validation"
                        )
                    ),
                },
                "package": {
                    "id": self.model_package.manifest.package_id,
                    "version": self.model_package.manifest.package_version,
                    "manifest_sha256": self.model_package.manifest_sha256,
                    "runtime_types": runtime_types,
                },
                "feature_pipeline": {
                    "id": self.model_package.manifest.feature_pipeline.id,
                    "version": self.model_package.manifest.feature_pipeline.version,
                    "input_schema_version": "canonical-candidate/v1",
                    "features": list(values),
                },
                "training_data": {
                    "source_path": self.data.source_path,
                    "source_sha256": self.data.source_sha256,
                    "records": self.training_stats["records"],
                },
                "prediction_interval": {
                    "method": (
                        "grouped out-of-fold calibrated normal interval"
                        if uses_monotone_lightgbm
                        else (
                            "grouped out-of-fold residual quantiles"
                            if self.profile.group_column
                            else "row-wise out-of-fold residual quantiles"
                        )
                    ),
                    "coverage": "central 90% empirical interval",
                    "grouping": self.profile.group_column or "independent source row",
                },
                "similarity": {
                    "version": self.support_policy_id,
                    "method": "nearest row in standardized feature space",
                },
            },
            "heat_pattern": [],
            "response_curve": None,
        }

    def predict(self, candidate: Candidate, detailed: bool = False, **kwargs: Any) -> dict[str, Any]:
        kwargs.pop("include_curve", None)
        result = self.predict_core(candidate, detailed=detailed, **kwargs)
        support, similar = self.evidence(candidate)
        result["support"] = support
        result["similar"] = similar
        if support.status != "supported":
            result["warnings"].append(support.message)
        return result

    def response_curve_result(
        self,
        candidate: Candidate,
        target: str,
        variable: str,
        points: int,
        axis_range: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        if target not in self.predictors:
            raise ValueError(f"Unsupported response-curve target: {target}")
        item = next((item for item in self.profile.inputs if item.path == variable and item.kind == "number"), None)
        if item is None:
            raise ValueError(f"この予測タスクで応答曲線にできない変数です: {variable}")
        training = [
            float((row["composition"] if variable.startswith("composition.") else row["features"])[variable.split(".", 1)[1]])
            for row in self.reference_rows
        ]
        current = float(_get_path(candidate, variable))
        low, high = min(training), max(training)
        if axis_range is not None:
            low, high = axis_range
        curve = []
        for x_value in anchored_curve_grid(low, high, points, current=current):
            adjusted = candidate.model_copy(deep=True)
            _set_path(adjusted, variable, float(x_value))
            summary = self.predictors[target].predict(
                build_tabular_features(adjusted, self.profile).as_dict()
            )
            lower, upper = predictive_interval(summary)
            output_profile = next(item for item in self.profile.outputs if item.key == target)
            value = summary.point_estimate
            if output_profile.lower_bound is not None:
                value = max(output_profile.lower_bound, value)
                lower = max(output_profile.lower_bound, lower)
                upper = max(output_profile.lower_bound, upper)
            if output_profile.upper_bound is not None:
                value = min(output_profile.upper_bound, value)
                lower = min(output_profile.upper_bound, lower)
                upper = min(output_profile.upper_bound, upper)
            curve.append({
                "x": round(float(x_value), 5),
                "value": round(value, 5),
                "lower": round(lower, 5),
                "upper": round(upper, 5),
                "target_kind": summary.target_kind,
                "point_statistic": summary.point_statistic,
                "predictive_family": summary.distribution.get("family", "empirical_quantiles"),
                "quantiles": {
                    "0.05": round(lower, 5),
                    "0.95": round(upper, 5),
                },
            })
        definition = load_task_definitions()[self.task_id]
        field = next(field for group in definition.input_groups for field in group.fields if field.path == variable)
        observed = [float(row["outputs"][target]) for row in self.reference_rows]
        return {
            "target": target,
            "variable": {
                "id": variable,
                "label": field.label,
                "unit": field.unit or "",
                "min": low,
                "max": high,
                "current": current,
            },
            "points": curve,
            "output_range": {"min": min(observed), "max": max(observed)},
            "point_count": points,
            "policy_id": "anchored-axis-grid-v1",
        }

    def curve_family_result(
        self,
        candidate: Candidate,
        target: str,
        vary_variable: str | None,
        levels: int,
        points: int,
    ) -> dict[str, Any]:
        axis = self.profile.curve_axis_path
        if axis is None:
            raise ValueError("この予測タスクには曲線軸がありません")
        axis_result = self.response_curve_result(candidate, target, axis, points)
        series: list[dict[str, Any]] = []
        vary_meta: dict[str, Any] | None = None
        vary_categorical: dict[str, Any] | None = None
        if not vary_variable:
            series.append({"level": None, "label": "現在の候補", "points": axis_result["points"]})
        else:
            item = next((item for item in self.profile.inputs if item.path == vary_variable), None)
            if item is None or vary_variable == axis:
                raise ValueError("曲線の水準にできない変数です")
            if item.kind == "categorical":
                current = str(_get_path(candidate, vary_variable))
                vary_categorical = {
                    "id": vary_variable,
                    "label": vary_variable.split(".", 1)[1],
                    "choices": list(item.choices),
                    "current": current,
                }
                for choice in item.choices:
                    adjusted = candidate.model_copy(deep=True)
                    adjusted.inputs.categorical[vary_variable.split(".", 1)[1]] = choice
                    curve = self.response_curve_result(adjusted, target, axis, points)
                    series.append({"level": choice, "label": choice, "points": curve["points"]})
            else:
                training = [
                    float((row["composition"] if vary_variable.startswith("composition.") else row["features"])[vary_variable.split(".", 1)[1]])
                    for row in self.reference_rows
                ]
                low, high = min(training), max(training)
                vary_meta = {
                    "id": vary_variable,
                    "label": vary_variable.split(".", 1)[1],
                    "unit": item.unit,
                    "min": low,
                    "max": high,
                    "current": float(_get_path(candidate, vary_variable)),
                }
                for level in np.linspace(low, high, levels):
                    adjusted = candidate.model_copy(deep=True)
                    _set_path(adjusted, vary_variable, float(level))
                    curve = self.response_curve_result(adjusted, target, axis, points)
                    unit = f" {item.unit}" if item.unit else ""
                    series.append({
                        "level": round(float(level), 5),
                        "label": f"{vary_meta['label']} {float(level):g}{unit}",
                        "points": curve["points"],
                    })
        return {
            "target": target,
            "axis": axis_result["variable"],
            "vary": vary_meta,
            "vary_categorical": vary_categorical,
            "series": series,
            "output_range": axis_result["output_range"],
            "point_count": points,
            "policy_id": "anchored-axis-grid-v1",
        }
