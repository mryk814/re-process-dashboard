from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from material_workbench.contracts.feature_contracts import feature_index_families
from material_workbench.modeling.hot_rolling_feature_pipeline import (
    FEATURE_DEFINITIONS,
    FEATURE_NAMES,
    INPUT_SCHEMA_VERSION,
    PIPELINE_ID,
    PIPELINE_VERSION,
    V2_FEATURE_DEFINITIONS,
    V2_FEATURE_NAMES,
    V2_PIPELINE_VERSION,
    build_hot_rolling_features,
    build_hot_rolling_features_v2,
    candidate_from_observation,
)
from material_workbench.data.dataset_profile import load_task_definitions
from material_workbench.data.importer import WorkbookData, lineage_reference_keys, training_context_key
from material_workbench.modeling.model_packages import ModelPackageLoader, VerifiedModelPackage, predictive_interval, validate_predictive_summary, validate_task_definition_canonical_inputs
from material_workbench.domain.goal_targets import goal_fields, normal_goal_probability
from material_workbench.contracts.schemas import Candidate, CandidateInput, Prediction, Support, TargetValue
from material_workbench.tasks.task_registry import load_task_contracts


TASK_ID = "hot-rolled-properties-v1"
SUPPORT_POLICY_ID = "hot-rolling-parent-condition-knn-v1"
FEATURE_GROUP_INDICES = feature_index_families(
    FEATURE_DEFINITIONS,
    {
        "composition": ("composition",),
        "metallurgy": ("metallurgy",),
        "process": ("process",),
    },
)


def _distance(
    reference: np.ndarray,
    point: np.ndarray,
    columns: tuple[int, ...] | None = None,
    groups: dict[str, tuple[int, ...]] = FEATURE_GROUP_INDICES,
) -> np.ndarray:
    if columns is not None:
        return np.sqrt(((reference[:, columns] - point[list(columns)]) ** 2).mean(axis=1))
    parts = [((reference[:, columns] - point[list(columns)]) ** 2).mean(axis=1) for columns in groups.values()]
    return np.sqrt(np.vstack(parts).mean(axis=0))


class HotRollingRuntime:
    task_id = TASK_ID
    support_policy_id = SUPPORT_POLICY_ID

    def __init__(
        self,
        data: WorkbookData,
        package_root: str | Path | VerifiedModelPackage | None = None,
    ) -> None:
        self.data = data
        self.task_definition = load_task_definitions()[TASK_ID]
        default = Path(__file__).resolve().parents[4] / "models" / "packages" / "hot-rolled-tutorial-v2"
        selected_package = package_root or default
        self.model_package = (
            selected_package
            if isinstance(selected_package, VerifiedModelPackage)
            else ModelPackageLoader().load(selected_package)
        )
        manifest = self.model_package.manifest
        self.feature_names = FEATURE_NAMES
        self.feature_definitions = FEATURE_DEFINITIONS
        self.pipeline_version = PIPELINE_VERSION
        self._feature_builder = build_hot_rolling_features
        validate_task_definition_canonical_inputs(self.task_definition, manifest)
        if manifest.task_id != TASK_ID:
            raise ValueError(f"Model package task {manifest.task_id} is incompatible with {TASK_ID}")
        if manifest.feature_pipeline.id != PIPELINE_ID:
            raise ValueError("Hot-rolling model package feature pipeline is incompatible")
        if manifest.feature_pipeline.version == V2_PIPELINE_VERSION:
            self.feature_names = V2_FEATURE_NAMES
            self.feature_definitions = V2_FEATURE_DEFINITIONS
            self.pipeline_version = V2_PIPELINE_VERSION
            self._feature_builder = build_hot_rolling_features_v2
        elif manifest.feature_pipeline.version != PIPELINE_VERSION:
            raise ValueError("Hot-rolling model package feature pipeline is incompatible")
        if tuple(manifest.feature_pipeline.output_features) != self.feature_names:
            raise ValueError("Hot-rolling model package feature order is incompatible")
        self.feature_group_indices = feature_index_families(
            self.feature_definitions,
            {
                "composition": ("composition",),
                "metallurgy": ("metallurgy",),
                "process": ("process",),
            },
        )
        stats_path = next(path for path in manifest.feature_pipeline.artifacts if path.endswith("training_stats.json"))
        stats = json.loads(self.model_package.artifact_path(stats_path).read_text(encoding="utf-8"))
        self.composition_defaults = {name: float(value) for name, value in stats["composition_defaults"].items()}
        self.training_counts = {name: int(value) for name, value in stats["records"].items()}
        self.predictors = {spec.target: self.model_package.load_predictor(spec.id) for spec in manifest.predictors}
        self._verify_package_smoke()
        self._build_support_reference()

    def _verify_package_smoke(self) -> None:
        smoke = self.model_package.manifest.smoke_test
        if not smoke:
            raise ValueError("Hot-rolling model package must declare a smoke test")
        candidate = CandidateInput.model_validate(json.loads(self.model_package.artifact_path(smoke.input).read_text(encoding="utf-8")))
        expected = json.loads(self.model_package.artifact_path(smoke.expected).read_text(encoding="utf-8"))
        values = self._feature_builder(candidate, self.composition_defaults).as_dict()
        specs = {spec.target: spec for spec in self.model_package.manifest.predictors}
        capabilities = {item.target: item for item in load_task_contracts()[TASK_ID].runtime_capability.targets}
        summaries = {target: predictor.predict(values) for target, predictor in self.predictors.items()}
        for target, summary in summaries.items():
            validate_predictive_summary(summary, specs[target], capabilities[target])
        actual = {target: summary.point_estimate for target, summary in summaries.items()}
        if set(actual) != set(expected) or any(not np.isclose(actual[target], expected[target], rtol=1e-7, atol=1e-7) for target in actual):
            raise ValueError("Hot-rolling model package smoke test did not reproduce expected predictions")

    def _build_support_reference(self) -> None:
        observations = [
            row for row in self.data.observations
            if row["task_id"] == TASK_ID and row["eligible"] and row["features"] and row["composition"]
        ]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in observations:
            grouped[training_context_key(row)].append(row)
        self.reference_rows = [rows for _, rows in sorted(grouped.items())]
        bundles = [
            self._feature_builder(candidate, self.composition_defaults)
            if (candidate := candidate_from_observation(rows[0])) is not None else None
            for rows in self.reference_rows
        ]
        if any(bundle is None for bundle in bundles):
            raise ValueError("eligible hot-rolling observations must convert to candidates")
        raw = np.vstack([
            bundle.values
            for bundle in bundles
            if bundle is not None
        ])
        self.reference_mean = raw.mean(axis=0)
        self.reference_scale = raw.std(axis=0)
        self.reference_scale[self.reference_scale < 1e-9] = 1.0
        self.reference_vectors = (raw - self.reference_mean) / self.reference_scale
        pairwise = np.sqrt(((self.reference_vectors[:, None, :] - self.reference_vectors[None, :, :]) ** 2).mean(axis=2))
        np.fill_diagonal(pairwise, np.inf)
        self.loo_nearest = pairwise.min(axis=1)

    def vector(self, candidate: CandidateInput) -> np.ndarray:
        return self._feature_builder(candidate, self.composition_defaults).values

    def _support(self, candidate: CandidateInput, *, include_similarity: bool = True) -> tuple[Support, list[dict[str, Any]]]:
        normalized = (self.vector(candidate) - self.reference_mean) / self.reference_scale
        distances = _distance(self.reference_vectors, normalized, groups=self.feature_group_indices)
        nearest_index = int(np.argmin(distances))
        nearest = float(distances[nearest_index])
        supported_limit, caution_limit = (float(value) for value in np.quantile(self.loo_nearest, (0.80, 0.95)))
        if nearest <= supported_limit:
            status, message = "supported", "近い熱延条件に有効な実測があります"
        elif nearest <= caution_limit:
            status, message = "caution", "近傍はありますが、熱延条件の密度が低い領域です"
        else:
            status, message = "extrapolated", "学習済みの熱延条件から外れています。予測値は探索的な参考です"
        nearest_rows: list[dict[str, Any]] = []
        if include_similarity:
            for index in np.argsort(distances)[:3]:
                repeats = self.reference_rows[int(index)]
                values: dict[str, list[float]] = defaultdict(list)
                for repeat in repeats:
                    for name, value in repeat["outputs"].items():
                        values[name].append(float(value))
                nearest_rows.append({
                    "observation_id": repeats[0]["id"],
                    "observation_ids": [repeat["id"] for repeat in repeats],
                    "source": " / ".join(sorted({str(repeat["source"]) for repeat in repeats})),
                    "layer": "training",
                    "parent_key": repeats[0]["parent_key"],
                    "test_direction": repeats[0].get("test_direction"),
                    "distance": round(float(distances[index]), 4),
                    "components": {
                        name: round(float(_distance(self.reference_vectors, normalized, columns)[int(index)]), 4)
                        for name, columns in self.feature_group_indices.items()
                    },
                    "repeat_summary": {
                        name: {"mean": round(float(np.mean(items)), 3), "std": round(float(np.std(items)), 3), "n": len(items)}
                        for name, items in sorted(values.items())
                    },
                    **lineage_reference_keys(
                        self.data,
                        str(repeats[0]["parent_key"]),
                        "hot_rolling",
                        repeats[0],
                    ),
                })
        return Support(
            status=status,
            distance=round(nearest, 4),
            percentile=round(float((self.loo_nearest <= nearest).mean() * 100), 1),
            message=message,
            components={name: round(float(_distance(self.reference_vectors, normalized, columns)[nearest_index]), 4) for name, columns in self.feature_group_indices.items()},
            reference_count=len(self.reference_rows),
            supported_threshold=round(supported_limit, 4),
            caution_threshold=round(caution_limit, 4),
        ), nearest_rows

    @property
    def output_keys(self) -> frozenset[str]:
        return frozenset(self.predictors)

    def predict_core(
        self,
        candidate: Candidate,
        detailed: bool = False,
        target_values: dict[str, TargetValue] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        bundle = self._feature_builder(candidate, self.composition_defaults)
        values = bundle.as_dict()
        predictions: dict[str, Prediction] = {}
        for target, predictor in self.predictors.items():
            summary = predictor.predict(values)
            output = next(item for item in self.task_definition.outputs if item.key == target)
            lower, upper = predictive_interval(summary)
            goal = (target_values or {}).get(target)
            goal_probability = None
            standard_deviation = float(summary.distribution.get("std", 0.0))
            if goal is not None and summary.distribution.get("family") == "normal" and standard_deviation > 0:
                goal_probability = normal_goal_probability(
                    summary.point_estimate, standard_deviation, goal, output.goal_direction
                )
            goal_value, goal_lower, goal_upper, goal_direction = goal_fields(goal, output.goal_direction)
            predictions[target] = Prediction(
                value=round(summary.point_estimate, 3),
                lower=round(lower, 3),
                upper=round(upper, 3),
                unit=summary.unit,
                target_kind=summary.target_kind,
                point_statistic=summary.point_statistic,
                predictive_family=summary.distribution.get("family", "empirical_quantiles"),
                quantiles={level: round(float(item), 6) for level, item in summary.quantiles.items()},
                categories=list(summary.distribution.get("categories", [])),
                goal_value=goal_value,
                goal_lower=goal_lower,
                goal_upper=goal_upper,
                goal_probability=None if goal_probability is None else round(goal_probability, 4),
                goal_direction=goal_direction,
                uncertainty_components=None if summary.uncertainty_components is None else {
                    name: round(float(value), 6) for name, value in summary.uncertainty_components.items()
                },
            )
        process = {**candidate.inputs.process}
        is_horseshoe = any(
            item.runtime_type == "builtin.posterior_linear.v1" and item.config.get("method") == "regularized_horseshoe"
            for item in self.model_package.manifest.predictors
        )
        is_tiny_demo = any(
            item.runtime_type == "builtin.posterior_linear.v1" and item.config.get("method") == "tiny_demo_ridge_posterior"
            for item in self.model_package.manifest.predictors
        )
        model_method = (
            "Regularized Horseshoe sparse Bayesian regression"
            if is_horseshoe
            else "Ridge approximate posterior for tiny teaching data"
            if is_tiny_demo
            else "Gaussian process regression"
        )
        interval_identity = (
            {
                "method": "posterior_predictive_moment_matched_normal",
                "coverage": "central 90% predictive interval",
                "grouping": "condition_context_id",
                "note": (
                    "Horseshoe posterior draws are summarized as a moment-matched Normal distribution for the shared decision UI."
                    if is_horseshoe
                    else "The tiny teaching posterior is summarized as a moment-matched Normal distribution for the shared decision UI."
                ),
            }
            if is_horseshoe or is_tiny_demo
            else {
                "method": "gaussian_process_predictive_distribution",
                "coverage": "central 90% predictive interval",
                "grouping": "condition_context_id",
                "note": "Model uncertainty and observation noise are reported separately.",
            }
        )
        return {
            "task_id": self.task_id,
            "candidate_id": candidate.id,
            "mode": "detailed" if detailed else "preview",
            "predictions": predictions,
            "warnings": [],
            "canonical_input": {
                "input_schema_version": INPUT_SCHEMA_VERSION,
                "composition_mass_percent": {name: values[name] for name in self.composition_defaults},
                "process": process,
                "feature_engineering": bundle.explanation_rows(),
                "feature_vector": values,
            },
            "model_meta": {
                "task_id": self.task_id,
                "model": {"id": self.model_package.manifest.package_id, "version": self.model_package.manifest.package_version, "method": model_method},
                "package": {"id": self.model_package.manifest.package_id, "version": self.model_package.manifest.package_version, "manifest_sha256": self.model_package.manifest_sha256, "runtime_types": sorted({item.runtime_type for item in self.model_package.manifest.predictors})},
                "feature_pipeline": {"id": PIPELINE_ID, "version": self.pipeline_version, "input_schema_version": INPUT_SCHEMA_VERSION, "features": list(self.feature_names)},
                "training_data": {"source_path": self.data.source_path, "source_sha256": self.data.source_sha256, "records": self.training_counts, "package_training_data_id": self.model_package.manifest.provenance.training_data_id, "package_feature_dataset_id": self.model_package.manifest.provenance.feature_dataset_id},
                "prediction_interval": interval_identity,
                "similarity": {"version": SUPPORT_POLICY_ID, "method": "parent-condition nearest-neighbor distance over composition, metallurgy, and process feature groups"},
            },
            "heat_pattern": [],
            "response_curve": None,
        }

    def evidence(self, candidate: Candidate) -> tuple[Support, list[dict[str, Any]]]:
        return self._support(candidate)

    def support_summary(self, candidate: Candidate) -> Support:
        return self._support(candidate, include_similarity=False)[0]

    def support_by_target(self, candidate: Candidate) -> dict[str, Support]:
        support = self.support_summary(candidate)
        return {target: support for target in sorted(self.output_keys)}

    def similarity(self, candidate: Candidate, limit: int = 3, target: str | None = None) -> list[dict[str, Any]]:
        return self.evidence(candidate)[1][:limit]

    def _response_curve_field(self, variable: str) -> tuple[Any, str, str]:
        declared = {
            item.path
            for item in self.task_definition.response_curve_variables
            if item.kind == "numeric_input"
        }
        if variable not in declared:
            raise ValueError(f"Unsupported response-curve variable: {variable}")
        group, name = variable.split(".", 1)
        field = next(
            (
                field
                for input_group in self.task_definition.input_groups
                for field in input_group.fields
                if field.path == variable
            ),
            None,
        )
        if field is None or field.training_range is None or field.allowed_range is None:
            raise ValueError(f"Response-curve variable has no numeric range: {variable}")
        return field, group, name

    @staticmethod
    def _candidate_value(candidate: Candidate, path: str) -> float:
        group, name = path.split(".", 1)
        values = candidate.inputs.composition if group == "composition" else candidate.inputs.process
        return float(values[name])

    def _validate_response_curve_value(self, candidate: Candidate, variable: str, value: float) -> None:
        field, _, _ = self._response_curve_field(variable)
        assert field.allowed_range is not None
        if not field.allowed_range.min <= value <= field.allowed_range.max:
            raise ValueError(
                f"{field.label}は{field.allowed_range.min:g}〜{field.allowed_range.max:g}{field.unit or ''}で指定してください"
            )
        values = {
            input_field.path: (value if input_field.path == variable else self._candidate_value(candidate, input_field.path))
            for input_group in self.task_definition.input_groups
            for input_field in input_group.fields
            if input_field.kind == "number"
        }
        comparisons = {
            "lt": lambda left, right: left < right,
            "lte": lambda left, right: left <= right,
            "gt": lambda left, right: left > right,
            "gte": lambda left, right: left >= right,
        }
        for constraint in self.task_definition.constraints:
            if not comparisons[constraint.operator](values[constraint.left_path], values[constraint.right_path]):
                raise ValueError(constraint.message)

    def _response_curve_bounds(self, candidate: Candidate, variable: str) -> tuple[float, float, float]:
        field, _, _ = self._response_curve_field(variable)
        assert field.allowed_range is not None
        decimals = self.task_definition.display_decimals[variable]
        epsilon = 10.0 ** -decimals
        lower, upper = field.allowed_range.min, field.allowed_range.max
        for constraint in self.task_definition.constraints:
            if variable == constraint.left_path:
                other = self._candidate_value(candidate, constraint.right_path)
                if constraint.operator == "lt":
                    upper = min(upper, other - epsilon)
                elif constraint.operator == "lte":
                    upper = min(upper, other)
                elif constraint.operator == "gt":
                    lower = max(lower, other + epsilon)
                else:
                    lower = max(lower, other)
            elif variable == constraint.right_path:
                other = self._candidate_value(candidate, constraint.left_path)
                if constraint.operator == "lt":
                    lower = max(lower, other + epsilon)
                elif constraint.operator == "lte":
                    lower = max(lower, other)
                elif constraint.operator == "gt":
                    upper = min(upper, other - epsilon)
                else:
                    upper = min(upper, other)
        if lower >= upper:
            raise ValueError(f"{field.label}を動かせる有効な範囲がありません")
        return lower, upper, epsilon

    def response_curve_result(
        self,
        candidate: Candidate,
        target: str,
        variable: str,
        points: int,
        axis_range: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        if target not in self.output_keys:
            raise ValueError(f"Unsupported response-curve target: {target}")
        field, group, name = self._response_curve_field(variable)
        assert field.training_range is not None
        if axis_range is None:
            lower, upper, epsilon = self._response_curve_bounds(candidate, variable)
            start = max(lower, field.training_range.min)
            end = min(upper, field.training_range.max)
            if end - start < epsilon:
                current = self._candidate_value(candidate, variable)
                span = max((field.training_range.max - field.training_range.min) * 0.1, epsilon * 2)
                start = max(lower, current - span)
                end = min(upper, current + span)
        else:
            start, end = axis_range
        if start >= end:
            raise ValueError(f"{field.label}を動かせる有効な範囲がありません")
        self._validate_response_curve_value(candidate, variable, float(start))
        self._validate_response_curve_value(candidate, variable, float(end))

        curve: list[dict[str, Any]] = []
        predictor = self.predictors[target]
        current = self._candidate_value(candidate, variable)
        for x_value in anchored_curve_grid(start, end, points, current=current):
            adjusted = candidate.model_copy(deep=True)
            values = adjusted.inputs.composition if group == "composition" else adjusted.inputs.process
            values[name] = float(x_value)
            summary = predictor.predict(self._feature_builder(adjusted, self.composition_defaults).as_dict())
            value = summary.point_estimate
            interval_lower, interval_upper = predictive_interval(summary)
            curve.append({
                "x": round(float(x_value), 4),
                "value": round(value, 3),
                "lower": round(interval_lower, 3),
                "upper": round(interval_upper, 3),
                "target_kind": summary.target_kind,
                "point_statistic": summary.point_statistic,
                "predictive_family": summary.distribution.get("family", "empirical_quantiles"),
                "quantiles": {level: round(float(item), 6) for level, item in summary.quantiles.items()},
                "categories": list(summary.distribution.get("categories", [])),
            })

        output = next(item for item in self.task_definition.outputs if item.key == target)
        observed = [
            float(row["outputs"][measurement_key])
            for rows in self.reference_rows
            for row in rows
            for measurement_key in output.measurement_keys
            if measurement_key in row["outputs"]
        ]
        return {
            "target": target,
            "variable": {
                "id": variable,
                "label": field.label,
                "unit": field.unit or "",
                "min": round(float(start), 4),
                "max": round(float(end), 4),
                "current": round(self._candidate_value(candidate, variable), 4),
                "training_range": {
                    "min": round(field.training_range.min, 4),
                    "max": round(field.training_range.max, 4),
                },
            },
            "points": curve,
            "output_range": None if not observed else {"min": round(min(observed), 4), "max": round(max(observed), 4)},
            "point_count": points,
            "policy_id": "anchored-grid-v1",
        }

    def predict(self, candidate: Candidate, detailed: bool = False, **kwargs: Any) -> dict[str, Any]:
        result = self.predict_core(candidate, detailed=detailed, **kwargs)
        support, similar = self.evidence(candidate)
        result["support"] = support
        result["similar"] = similar
        if support.status != "supported":
            result["warnings"].append(support.message)
        return result
from material_workbench.modeling.curve_grid import anchored_curve_grid
