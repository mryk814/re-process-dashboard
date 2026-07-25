from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from material_workbench.contracts.feature_contracts import feature_index_families
from material_workbench.modeling.feature_pipeline import (
    FEATURE_DEFINITIONS,
    FEATURE_NAMES as METALLURGY_FEATURE_NAMES,
    FEATURE_PIPELINE_ID,
    FEATURE_PIPELINE_VERSION,
    V2_FEATURE_DEFINITIONS,
    V2_FEATURE_NAMES,
    V2_FEATURE_PIPELINE_VERSION,
    build_feature_bundle,
    build_feature_bundle_v2,
    candidate_from_observation,
)
from material_workbench.domain.heat_time import line_speed_scaled_times
from material_workbench.data.dataset_profile import load_task_definitions
from material_workbench.data.importer import WorkbookData, composition_names, lineage_reference_keys, training_context_key
from material_workbench.modeling.model_packages import ModelPackageLoader, VerifiedModelPackage, predictive_interval, validate_predictive_summary, validate_task_definition_canonical_inputs
from material_workbench.domain.goal_targets import empirical_goal_probability, goal_fields, normal_goal_probability
from material_workbench.contracts.schemas import Candidate, CandidateInput, HeatPoint, Prediction, Support, TargetRange, TargetValue
from material_workbench.tasks.task_registry import load_task_contracts


TASK_ID = "annealed-properties-v1"
COMPOSITION_COLUMNS = composition_names(task_id=TASK_ID)
MODEL_ID = "anneal-ridge"
MODEL_VERSION = "0.4.0-oof-v1"
SIMILARITY_VERSION = "parent-condition-knn-v3-repeat-summary"
INPUT_SCHEMA_VERSION = "candidate-v2"
TARGETS = {"TS": ("TS[MPa]", "MPa"), "YS": ("YS[MPa]", "MPa"), "EL": ("EL[%]", "%"), "lambda": ("λ[%]", "%")}
HEAT_STAGE_TEMPERATURE_VARIABLE = "heat.stage_temperature_c"
FEATURE_NAMES = METALLURGY_FEATURE_NAMES
FEATURE_GROUP_INDICES = feature_index_families(
    FEATURE_DEFINITIONS,
    {
        "composition": ("composition",),
        "metallurgy": ("metallurgy",),
        "heat_pattern": ("heat_pattern",),
    },
)


def _fit_ridge(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-9] = 1.0
    normalized = (x - mean) / scale
    design = np.column_stack([np.ones(len(normalized)), normalized])
    ridge = np.eye(design.shape[1])
    ridge[0, 0] = 0.0
    weights = np.linalg.solve(design.T @ design + ridge, design.T @ y)
    return mean, scale, weights


def _predict_ridge(x: np.ndarray, mean: np.ndarray, scale: np.ndarray, weights: np.ndarray) -> np.ndarray:
    normalized = (x - mean) / scale
    return np.column_stack([np.ones(len(normalized)), normalized]) @ weights


def _grouped_oof_residuals(x: np.ndarray, y: np.ndarray, groups: list[str]) -> tuple[np.ndarray, int]:
    """Return deterministic grouped OOF residuals; repeats never train their own parent condition."""
    unique_groups = sorted(set(groups))
    folds = min(5, len(unique_groups))
    if folds < 2:
        raise ValueError("At least two independent parent conditions are required for OOF calibration")
    fold_for_group = {group: index % folds for index, group in enumerate(unique_groups)}
    residuals = np.empty(len(y), dtype=float)
    group_array = np.asarray(groups)
    for fold in range(folds):
        test_mask = np.asarray([fold_for_group[group] == fold for group in groups])
        train_mask = ~test_mask
        mean, scale, weights = _fit_ridge(x[train_mask], y[train_mask])
        residuals[test_mask] = y[test_mask] - _predict_ridge(x[test_mask], mean, scale, weights)
    return residuals, folds


def _rms_distance(
    reference: np.ndarray,
    point: np.ndarray,
    columns: tuple[int, ...] | None = None,
    groups: dict[str, tuple[int, ...]] = FEATURE_GROUP_INDICES,
) -> np.ndarray:
    if columns is not None:
        return np.sqrt(((reference[:, columns] - point[list(columns)]) ** 2).mean(axis=1))
    # Give composition, process, metallurgy and heat-history evidence equal
    # influence regardless of how many scalar features each group contains.
    component_mse = [
        ((reference[:, group] - point[list(group)]) ** 2).mean(axis=1)
        for group in groups.values()
    ]
    return np.sqrt(np.vstack(component_mse).mean(axis=0))


@dataclass
class RidgeModel:
    target: str
    unit: str
    feature_mean: np.ndarray
    feature_scale: np.ndarray
    weights: np.ndarray
    oof_residuals: np.ndarray
    x_train: np.ndarray
    rows: list[dict[str, Any]]
    calibration_folds: int

    def predict(self, x: np.ndarray) -> float:
        return float(_predict_ridge(x.reshape(1, -1), self.feature_mean, self.feature_scale, self.weights)[0])

    def interval_offsets(self) -> tuple[float, float]:
        lower, upper = np.quantile(self.oof_residuals, (0.05, 0.95))
        return float(lower), float(upper)


@dataclass
class SupportReference:
    feature_mean: np.ndarray
    feature_scale: np.ndarray
    parent_vectors: np.ndarray
    parent_rows: list[dict[str, Any]]
    parent_observation_rows: list[list[dict[str, Any]]]
    loo_nearest_distances: np.ndarray

    def normalized(self, x: np.ndarray) -> np.ndarray:
        return (x - self.feature_mean) / self.feature_scale


class ModelRuntime:
    task_id = TASK_ID
    support_policy_id = SIMILARITY_VERSION

    def __init__(
        self,
        data: WorkbookData,
        package_root: str | Path | VerifiedModelPackage | None = None,
        *,
        load_package: bool = True,
    ) -> None:
        self.data = data
        self.feature_names = FEATURE_NAMES
        self.feature_definitions = FEATURE_DEFINITIONS
        self.feature_pipeline_version = FEATURE_PIPELINE_VERSION
        self._feature_builder = build_feature_bundle
        self.feature_group_indices = FEATURE_GROUP_INDICES
        default_package = Path(__file__).resolve().parents[4] / "models" / "packages" / "annealed-gp-stable-ard-tutorial-v1"
        selected_package = package_root or default_package
        self.model_package: VerifiedModelPackage | None = (
            selected_package
            if load_package and isinstance(selected_package, VerifiedModelPackage)
            else ModelPackageLoader().load(selected_package)
            if load_package and (package_root or default_package.exists())
            else None
        )
        self.composition_defaults = dict(data.medians)
        self.composition_defaults_sha256: str | None = None
        if self.model_package:
            self._load_and_validate_package_feature_contract()
        self.models: dict[str, RidgeModel] = {}
        self.support_reference: SupportReference | None = None
        self.target_support_references: dict[str, SupportReference] = {}
        self.historical_reference: SupportReference | None = None
        self._fit()
        self.package_predictors = {
            spec.target: self.model_package.load_predictor(spec.id)
            for spec in self.model_package.manifest.predictors
        } if self.model_package else {}
        self.package_predictor_specs = {
            spec.target: spec for spec in self.model_package.manifest.predictors
        } if self.model_package else {}
        if self.model_package:
            self._verify_package_smoke()

    @property
    def output_keys(self) -> frozenset[str]:
        return frozenset(self.package_predictors if self.model_package else self.models)

    def _load_and_validate_package_feature_contract(self) -> None:
        assert self.model_package is not None
        manifest = self.model_package.manifest
        validate_task_definition_canonical_inputs(load_task_definitions()[TASK_ID], manifest)
        if manifest.task_id != TASK_ID:
            raise ValueError(f"Model package task {manifest.task_id} is incompatible with {TASK_ID}")
        if manifest.feature_pipeline.id != FEATURE_PIPELINE_ID:
            raise ValueError("Model package feature pipeline id/version is incompatible")
        if manifest.feature_pipeline.version == V2_FEATURE_PIPELINE_VERSION:
            self.feature_names = V2_FEATURE_NAMES
            self.feature_definitions = V2_FEATURE_DEFINITIONS
            self.feature_pipeline_version = V2_FEATURE_PIPELINE_VERSION
            self._feature_builder = build_feature_bundle_v2
        elif manifest.feature_pipeline.version != FEATURE_PIPELINE_VERSION:
            raise ValueError("Model package feature pipeline id/version is incompatible")
        expected = tuple(self.feature_names)
        available_groups = {item.group for item in self.feature_definitions}
        self.feature_group_indices = feature_index_families(
            self.feature_definitions,
            {
                name: selectors
                for name, selectors in {
                    "composition": ("composition",),
                    "process": ("process",),
                    "metallurgy": ("metallurgy",),
                    "heat_pattern": ("heat_pattern",),
                }.items()
                if any(selector in available_groups for selector in selectors)
            },
        )
        if tuple(manifest.feature_pipeline.output_features) != expected:
            raise ValueError("Model package feature order does not match the application pipeline")
        if any(tuple(predictor.feature_names) != expected for predictor in manifest.predictors):
            raise ValueError("Model package predictor feature order is inconsistent")
        pipeline = json.loads(self.model_package.artifact_path(manifest.feature_pipeline.spec).read_text(encoding="utf-8"))
        if (pipeline.get("id"), pipeline.get("version")) != (FEATURE_PIPELINE_ID, self.feature_pipeline_version):
            raise ValueError("Model package pipeline specification id/version is incompatible")
        if tuple(item["name"] for item in pipeline.get("features", [])) != expected:
            raise ValueError("Model package pipeline specification is inconsistent")
        stats_path = next((path for path in manifest.feature_pipeline.artifacts if path.endswith("training_stats.json")), None)
        if not stats_path:
            raise ValueError("Model package must include training composition defaults")
        stats = json.loads(self.model_package.artifact_path(stats_path).read_text(encoding="utf-8"))
        defaults = stats.get("composition_defaults")
        if not isinstance(defaults, dict) or set(defaults) != set(COMPOSITION_COLUMNS):
            raise ValueError("Model package composition defaults are incomplete")
        self.composition_defaults = {name: float(defaults[name]) for name in COMPOSITION_COLUMNS}
        artifact = next(item for item in manifest.artifacts if item.path == stats_path)
        self.composition_defaults_sha256 = artifact.sha256

    def _verify_package_smoke(self) -> None:
        assert self.model_package is not None
        smoke = self.model_package.manifest.smoke_test
        if not smoke:
            raise ValueError("Production model package must declare a smoke test")
        candidate = CandidateInput.model_validate(json.loads(self.model_package.artifact_path(smoke.input).read_text(encoding="utf-8")))
        expected = json.loads(self.model_package.artifact_path(smoke.expected).read_text(encoding="utf-8"))
        values = self._feature_builder(candidate, self.composition_defaults).as_dict()
        specs = {spec.target: spec for spec in self.model_package.manifest.predictors}
        capabilities = {item.target: item for item in load_task_contracts()[TASK_ID].runtime_capability.targets}
        summaries = {target: predictor.predict(values, seed=0) for target, predictor in self.package_predictors.items()}
        for target, summary in summaries.items():
            validate_predictive_summary(summary, specs[target], capabilities[target])
        actual = {target: summary.point_estimate for target, summary in summaries.items()}
        if set(actual) != set(expected) or any(not np.isclose(actual[target], expected[target], rtol=1e-7, atol=1e-7) for target in actual):
            raise ValueError("Model package smoke test did not reproduce its expected predictions")

    def vector_for_candidate(self, candidate: Candidate) -> np.ndarray:
        return self._feature_builder(candidate, self.composition_defaults).values.copy()

    def canonical_input(self, candidate: Candidate) -> dict[str, Any]:
        bundle = self._feature_builder(candidate, self.composition_defaults)
        vector = bundle.values.copy()
        feature_values = bundle.as_dict()
        normalized_vectors = {
            target: {name: round(float(value), 10) for name, value in zip(self.feature_names, (vector - model.feature_mean) / model.feature_scale)}
            for target, model in self.models.items()
        }
        return {
            "input_schema_version": INPUT_SCHEMA_VERSION,
            "composition_mass_percent": {name: round(float(vector[index]), 8) for index, name in enumerate(COMPOSITION_COLUMNS)},
            "composition_imputation": {
                "imputed_elements": [name for name in COMPOSITION_COLUMNS if name not in candidate.inputs.composition],
                "defaults": {name: self.composition_defaults[name] for name in COMPOSITION_COLUMNS},
                "reference_stats_sha256": self.composition_defaults_sha256,
            },
            "process": {
                **candidate.inputs.process,
                **candidate.inputs.categorical,
            },
            "heat_time_basis": candidate.inputs.heat_time_basis,
            "heat_pattern": [point.model_dump(mode="json") for point in (candidate.inputs.heat_pattern or [])],
            "heat_summary": {
                "peak_temperature_c": feature_values["peak_temperature_c"],
                "time_at_or_above_95pct_peak_s": feature_values["time_at_or_above_95pct_peak_s"],
                "time_at_or_above_700c_s": feature_values["time_at_or_above_700c_s"],
                "thermal_exposure_above_600c_c_s": feature_values["thermal_exposure_above_600c_c_s"],
                "cooling_rate_800_to_500_c_s": feature_values["cooling_rate_800_to_500_c_s"],
                "cooling_800_to_500_observed": bool(feature_values["cooling_800_to_500_observed"]),
                "reheat_count": int(feature_values["reheat_count"]),
                "has_reheat": bool(feature_values["has_reheat"]),
            },
            "feature_engineering": bundle.explanation_rows(),
            "feature_vector": {name: round(float(value), 10) for name, value in zip(self.feature_names, vector)},
            "normalized_feature_vectors": normalized_vectors,
        }

    def _vector_for_observation(self, row: dict[str, Any]) -> np.ndarray | None:
        candidate = candidate_from_observation(row)
        return None if candidate is None else self._feature_builder(candidate, self.data.medians).values.copy()

    @staticmethod
    def _support_reference(rows: list[dict[str, Any]], x: np.ndarray, mean: np.ndarray | None = None, scale: np.ndarray | None = None) -> SupportReference:
        mean = x.mean(axis=0) if mean is None else mean
        scale = x.std(axis=0) if scale is None else scale.copy()
        scale[scale < 1e-9] = 1.0
        normalized = (x - mean) / scale
        grouped_indexes: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(rows):
            grouped_indexes[training_context_key(row)].append(index)
        ordered_groups = sorted(grouped_indexes)
        parent_vectors = np.vstack([normalized[grouped_indexes[group]].mean(axis=0) for group in ordered_groups])
        parent_rows = [rows[grouped_indexes[group][0]] for group in ordered_groups]
        parent_observation_rows = [[rows[index] for index in grouped_indexes[group]] for group in ordered_groups]
        pairwise = np.sqrt(((parent_vectors[:, None, :] - parent_vectors[None, :, :]) ** 2).mean(axis=2))
        np.fill_diagonal(pairwise, np.inf)
        loo_nearest = pairwise.min(axis=1) if len(parent_vectors) > 1 else np.array([0.0])
        return SupportReference(mean, scale, parent_vectors, parent_rows, parent_observation_rows, loo_nearest)

    def _fit(self) -> None:
        prepared_all = [(row, self._vector_for_observation(row)) for row in self.data.observations]
        prepared_all = [(row, vector) for row, vector in prepared_all if vector is not None]
        prepared = [
            (row, vector) for row, vector in prepared_all
            if row["eligible"] and row["task_id"] == TASK_ID
        ]
        if prepared:
            self.support_reference = self._support_reference([row for row, _ in prepared], np.vstack([vector for _, vector in prepared]))
            if prepared_all:
                reference = self.support_reference
                self.historical_reference = self._support_reference(
                    [row for row, _ in prepared_all],
                    np.vstack([vector for _, vector in prepared_all]),
                    reference.feature_mean,
                    reference.feature_scale,
                )
        for label, (column, unit) in TARGETS.items():
            rows = [(row, vector, row["outputs"][column]) for row, vector in prepared if column in row["outputs"]]
            if len(rows) < 8:
                continue
            x = np.vstack([vector for _, vector, _ in rows])
            y = np.array([value for _, _, value in rows], dtype=float)
            groups = [training_context_key(row) for row, _, _ in rows]
            oof_residuals, folds = _grouped_oof_residuals(x, y, groups)
            mean, scale, weights = _fit_ridge(x, y)
            normalized = (x - mean) / scale
            self.models[label] = RidgeModel(label, unit, mean, scale, weights, oof_residuals, normalized, [row for row, _, _ in rows], folds)
            self.target_support_references[label] = self._support_reference(
                [row for row, _, _ in rows], x
            )

    def _support(
        self,
        x: np.ndarray,
        *,
        include_similarity: bool = True,
        reference: SupportReference | None = None,
    ) -> tuple[Support, list[dict[str, Any]]]:
        reference = reference or self.support_reference
        if reference is None:
            raise RuntimeError("No eligible observations are available for support estimation")
        normalized = reference.normalized(x)
        distances = _rms_distance(reference.parent_vectors, normalized, groups=self.feature_group_indices)
        nearest_index = int(np.argmin(distances))
        nearest = float(distances[nearest_index])
        loo = reference.loo_nearest_distances
        percentile = float((loo <= nearest).mean() * 100)
        supported_limit, caution_limit = (float(value) for value in np.quantile(loo, (0.80, 0.95)))
        if nearest <= supported_limit:
            status, message = "supported", "独立した過去条件の近傍に実測があります"
        elif nearest <= caution_limit:
            status, message = "caution", "近傍はありますが、過去条件の密度が低い領域です"
        else:
            status, message = "extrapolated", "独立した学習条件の近傍から外れています。予測値は探索的な参考です"
        components = {
            name: round(float(_rms_distance(reference.parent_vectors, normalized, columns)[nearest_index]), 4)
            for name, columns in self.feature_group_indices.items()
        }
        def nearest_rows(source: SupportReference, layer: str, limit: int, exclude: set[str] | None = None) -> list[dict[str, Any]]:
            source_normalized = source.normalized(x)
            source_distances = _rms_distance(source.parent_vectors, source_normalized, groups=self.feature_group_indices)
            rows: list[dict[str, Any]] = []
            for index in np.argsort(source_distances):
                row = source.parent_rows[int(index)]
                parent_key = str(row["parent_key"])
                if exclude and training_context_key(row) in exclude:
                    continue
                repeats = source.parent_observation_rows[int(index)]
                values_by_property: dict[str, list[float]] = defaultdict(list)
                for repeat in repeats:
                    for property_name, value in repeat["outputs"].items():
                        values_by_property[property_name].append(float(value))
                summaries = {
                    property_name: {
                        "mean": round(float(np.mean(values)), 3),
                        "std": round(float(np.std(values)), 3),
                        "n": len(values),
                    }
                    for property_name, values in sorted(values_by_property.items())
                }
                rows.append({
                    "observation_id": row["id"],
                    "observation_ids": [repeat["id"] for repeat in repeats],
                    "source": " / ".join(sorted({str(repeat["source"]) for repeat in repeats})),
                    "parent_key": parent_key,
                    "layer": layer, "distance": round(float(source_distances[index]), 4),
                    "components": {
                        name: round(float(_rms_distance(source.parent_vectors, source_normalized, columns)[int(index)]), 4)
                        for name, columns in self.feature_group_indices.items()
                    },
                    "outputs": {key: summary["mean"] for key, summary in summaries.items()},
                    "repeat_summary": summaries,
                    **lineage_reference_keys(self.data, parent_key, "annealing", row),
                })
                if len(rows) >= limit:
                    break
            return rows

        similar: list[dict[str, Any]] = []
        if include_similarity:
            training_nearest = nearest_rows(reference, "training", 3)
            training_contexts = {training_context_key(row) for row in reference.parent_rows}
            historical_nearest = nearest_rows(self.historical_reference, "historical", 3, training_contexts) if self.historical_reference else []
            similar = [*training_nearest, *historical_nearest]
        return Support(
            status=status,
            distance=round(nearest, 4),
            percentile=round(percentile, 1),
            message=message,
            components=components,
            reference_count=len(reference.parent_rows),
            supported_threshold=round(supported_limit, 4),
            caution_threshold=round(caution_limit, 4),
        ), similar

    def _model_meta(self) -> dict[str, Any]:
        meta = {
            "task_id": TASK_ID,
            "model": {"id": MODEL_ID, "version": MODEL_VERSION, "method": "ridge regression"},
            "feature_pipeline": {"id": FEATURE_PIPELINE_ID, "version": self.feature_pipeline_version, "input_schema_version": INPUT_SCHEMA_VERSION, "features": list(self.feature_names)},
            "training_data": {"source_path": self.data.source_path, "source_sha256": self.data.source_sha256, "source_mtime_ns": self.data.source_mtime_ns, "records": {name: len(model.rows) for name, model in self.models.items()}},
            "prediction_interval": {
                "method": "grouped_oof_residual_quantiles",
                "coverage": "empirical central 90% residual interval",
                "grouping": "condition_context_id",
                "folds": {name: model.calibration_folds for name, model in self.models.items()},
                "note": "Validation residuals are grouped by parent condition; this is not an uncertainty decomposition.",
            },
            "similarity": {"version": SIMILARITY_VERSION, "method": "parent-condition leave-one-out nearest-neighbor distance with equal weight for composition, process, metallurgy, and heat-pattern groups"},
        }
        if self.model_package:
            manifest = self.model_package.manifest
            meta["package"] = {"id": manifest.package_id, "version": manifest.package_version, "manifest_sha256": self.model_package.manifest_sha256, "runtime_types": sorted({item.runtime_type for item in manifest.predictors})}
            meta["model"] = {
                "id": manifest.package_id,
                "version": manifest.package_version,
                "method": " / ".join(sorted({f"{item.runtime_type}:{item.predictive_family}" for item in manifest.predictors})),
            }
            meta["feature_pipeline"] = {"id": manifest.feature_pipeline.id, "version": manifest.feature_pipeline.version, "input_schema_version": manifest.input_schema_version, "features": list(manifest.feature_pipeline.output_features)}
            meta["training_data"]["package_training_data_id"] = manifest.provenance.training_data_id
            if any(item.runtime_type in {"builtin.exact_gp.v1", "gpytorch.static_exact_rbf.v1"} for item in manifest.predictors):
                meta["prediction_interval"] = {
                    "method": "gaussian_process_predictive_distribution",
                    "coverage": "central 90% predictive interval",
                    "grouping": "condition_context_id",
                    "note": "Model uncertainty and observation noise are reported separately; data support remains an independent distance-based check.",
                }
        return meta

    def predict_core(self, candidate: Candidate, detailed: bool = False, target_values: dict[str, TargetValue] | None = None) -> dict[str, Any]:
        x = self.vector_for_candidate(candidate)
        predictions: dict[str, Prediction] = {}
        warnings: list[str] = []
        labels = self.package_predictors.keys() if self.package_predictors else self.models.keys()
        for label in labels:
            model = self.models.get(label)
            summary = None
            if label in self.package_predictors:
                summary = self.package_predictors[label].predict({name: float(value) for name, value in zip(self.feature_names, x)}, seed=0)
                value = summary.point_estimate
                lower, upper = predictive_interval(summary)
                unit = summary.unit
                warnings.extend(summary.warnings)
            else:
                assert model is not None
                value = model.predict(x)
                lower_offset, upper_offset = model.interval_offsets()
                lower, upper = value + lower_offset, value + upper_offset
                unit = model.unit
            goal = (target_values or {}).get(label)
            goal_probability = None
            if goal is not None and not isinstance(goal, TargetRange) and summary is not None and summary.event_probability is not None:
                goal_probability = summary.event_probability
            elif (
                goal is not None
                and summary is not None
                and summary.distribution.get("family") == "normal"
                and float(summary.distribution.get("std", 0.0)) > 0
            ):
                standard_deviation = float(summary.distribution["std"])
                goal_probability = normal_goal_probability(value, standard_deviation, goal, "at_least")
            elif goal is not None and summary is not None and model is not None:
                spec = self.package_predictor_specs[label]
                package_data_id = self.model_package.manifest.provenance.training_data_id if self.model_package else ""
                calibrated_same_data = (
                    spec.runtime_type == "builtin.linear.v1"
                    and spec.config.get("calibration") == "grouped_oof_residual_quantiles"
                    and package_data_id == f"sha256:{self.data.source_sha256}"
                )
                if calibrated_same_data:
                    goal_probability = empirical_goal_probability(value + model.oof_residuals, goal, "at_least")
            elif goal is not None and model is not None:
                goal_probability = empirical_goal_probability(value + model.oof_residuals, goal, "at_least")
            goal_value, goal_lower, goal_upper, goal_direction = goal_fields(goal, "at_least")
            predictions[label] = Prediction(
                value=round(value, 3), lower=round(lower, 3), upper=round(upper, 3), unit=unit,
                target_kind=summary.target_kind if summary is not None else "continuous",
                point_statistic=summary.point_statistic if summary is not None else "mean",
                predictive_family=summary.distribution.get("family", "empirical_quantiles") if summary is not None else "empirical_quantiles",
                quantiles={} if summary is None else {level: round(float(item), 6) for level, item in summary.quantiles.items()},
                categories=[] if summary is None else list(summary.distribution.get("categories", [])),
                goal_value=goal_value,
                goal_lower=goal_lower,
                goal_upper=goal_upper,
                goal_probability=None if goal_probability is None else round(goal_probability, 4),
                goal_direction=goal_direction,
                uncertainty_components=None if summary is None or summary.uncertainty_components is None else {
                    name: round(float(component), 6)
                    for name, component in summary.uncertainty_components.items()
                },
            )
        if candidate.inputs.composition.get("C", self.data.medians["C"]) > 1:
            warnings.append("C量が参照データの通常域から大きく外れています")
        return {
            "task_id": self.task_id, "candidate_id": candidate.id, "mode": "detailed" if detailed else "preview", "predictions": predictions,
            "warnings": warnings, "model_meta": self._model_meta(),
            "canonical_input": self.canonical_input(candidate),
            "heat_pattern": candidate.inputs.heat_pattern or [], "response_curve": None,
        }

    def evidence(self, candidate: Candidate) -> tuple[Support, list[dict[str, Any]]]:
        return self._support(self.vector_for_candidate(candidate))

    def support_summary(self, candidate: Candidate) -> Support:
        by_target = self.support_by_target(candidate)
        if not by_target:
            return self._support(self.vector_for_candidate(candidate), include_similarity=False)[0]
        severity = {"supported": 0, "caution": 1, "extrapolated": 2}
        return max(by_target.values(), key=lambda item: (severity[item.status], item.percentile, item.distance))

    def support_by_target(self, candidate: Candidate) -> dict[str, Support]:
        vector = self.vector_for_candidate(candidate)
        return {
            target: self._support(vector, include_similarity=False, reference=reference)[0]
            for target, reference in sorted(self.target_support_references.items())
        }

    def similarity(self, candidate: Candidate, limit: int = 6, target: str | None = None) -> list[dict[str, Any]]:
        return self.evidence(candidate)[1][:limit]

    def predict(self, candidate: Candidate, detailed: bool = False, include_curve: bool = False, target_values: dict[str, TargetValue] | None = None) -> dict[str, Any]:
        result = self.predict_core(candidate, detailed=detailed, target_values=target_values)
        support, similar = self.evidence(candidate)
        result["model_support"] = self.support_by_target(candidate)
        if support.status != "supported":
            result["warnings"].append(support.message)
        result["support"] = support
        result["similar"] = similar
        if include_curve:
            result["response_curve"] = self.response_curve(candidate)
        return result

    @staticmethod
    def _heat_stage_position(points: list[HeatPoint], line_speed_mpm: float, stage_position_m: float) -> float:
        if line_speed_mpm <= 0:
            raise ValueError("ラインスピードは0より大きくしてください")
        if stage_position_m < 0:
            raise ValueError("工程位置は0以上にしてください")
        target_time = points[0].time_s + 60.0 * stage_position_m / line_speed_mpm
        if target_time > points[-1].time_s + 1e-9:
            available_distance = (points[-1].time_s - points[0].time_s) * line_speed_mpm / 60.0
            raise ValueError(f"工程位置 {stage_position_m:g} m がヒートパターン範囲 {available_distance:g} m を超えています")
        return min(target_time, points[-1].time_s)

    @classmethod
    def _heat_stage_temperature(cls, points: list[HeatPoint], line_speed_mpm: float, stage_name: str, stage_position_m: float) -> float:
        matches = [point.temperature_c for point in points if (point.stage_name or "").strip() == stage_name]
        if matches:
            return float(sum(matches) / len(matches))
        target_time = cls._heat_stage_position(points, line_speed_mpm, stage_position_m)
        if target_time <= points[0].time_s:
            return float(points[0].temperature_c)
        if target_time >= points[-1].time_s:
            return float(points[-1].temperature_c)
        upper_index = next(index for index, point in enumerate(points) if point.time_s >= target_time)
        upper = points[upper_index]
        lower = points[upper_index - 1]
        if upper.segment_start and target_time < upper.time_s - 1e-9:
            raise ValueError(f"工程位置 {stage_position_m:g} m はヒートパターンの非連続な工程境界内にあります")
        ratio = (target_time - lower.time_s) / (upper.time_s - lower.time_s)
        return float(lower.temperature_c + ratio * (upper.temperature_c - lower.temperature_c))

    def _curve_variable_current(
        self,
        candidate: Candidate,
        variable: str,
        stage_name: str | None = None,
        stage_position_m: float | None = None,
    ) -> float:
        if variable.startswith("composition."):
            field = variable.removeprefix("composition.")
            if field not in COMPOSITION_COLUMNS:
                raise ValueError(f"Unsupported response-curve variable: {variable}")
            return float(candidate.inputs.composition.get(field, self.composition_defaults[field]))
        if variable.startswith("process."):
            field = variable.removeprefix("process.")
            if field not in candidate.inputs.process:
                raise ValueError(
                    f"{variable}はこの候補で未設定のため、応答曲線を作成できません"
                )
            return float(candidate.inputs.process[field])
        if variable == "heat.peak_temperature_c":
            return float(max(point.temperature_c for point in (candidate.inputs.heat_pattern or [])))
        if variable == HEAT_STAGE_TEMPERATURE_VARIABLE:
            points = candidate.inputs.heat_pattern or []
            if not points or not stage_name or stage_position_m is None:
                raise ValueError("工程温度の応答曲線には工程名と工程位置が必要です")
            line_speed = float(candidate.inputs.process.get("ls_mpm", 0.0))
            return self._heat_stage_temperature(points, line_speed, stage_name.strip(), stage_position_m)
        if variable.startswith("heat."):
            parts = variable.split(".")
            if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in {"time_min", "temperature_c"}:
                raise ValueError(f"Unsupported response-curve variable: {variable}")
            index = int(parts[1])
            points = candidate.inputs.heat_pattern or []
            if index >= len(points):
                raise ValueError(f"Unknown heat-pattern point: {variable}")
            return float(points[index].time_s / 60 if parts[2] == "time_min" else points[index].temperature_c)
        raise ValueError(f"Unsupported response-curve variable: {variable}")

    def _curve_training_values(
        self,
        model: RidgeModel,
        variable: str,
        stage_name: str | None = None,
        stage_position_m: float | None = None,
    ) -> list[float]:
        values: list[float] = []
        for row in model.rows:
            try:
                if variable.startswith("composition."):
                    value = row["composition"].get(variable.removeprefix("composition."))
                elif variable.startswith("process."):
                    value = row["features"].get(variable.removeprefix("process."))
                elif variable == "heat.peak_temperature_c":
                    value = max(point["temperature_c"] for point in row["features"].get("heat_pattern", []))
                elif variable == HEAT_STAGE_TEMPERATURE_VARIABLE:
                    raw_points = row["features"].get("heat_pattern", [])
                    if not raw_points or not stage_name or stage_position_m is None:
                        value = None
                    else:
                        heat_points = [HeatPoint.model_validate(point) for point in raw_points]
                        line_speed = float(row["features"].get("ls_mpm", 0.0))
                        value = self._heat_stage_temperature(heat_points, line_speed, stage_name.strip(), stage_position_m)
                elif variable.startswith("heat."):
                    parts = variable.split(".")
                    index = int(parts[1])
                    points = row["features"].get("heat_pattern", [])
                    raw_value = points[index].get("time_s" if parts[2] == "time_min" else parts[2]) if index < len(points) else None
                    value = float(raw_value) / 60 if raw_value is not None and parts[2] == "time_min" else raw_value
                else:
                    value = None
                if value is not None and np.isfinite(float(value)):
                    values.append(float(value))
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        return values

    def _curve_axis(
        self,
        candidate: Candidate,
        model: RidgeModel,
        variable: str,
        stage_name: str | None = None,
        stage_position_m: float | None = None,
    ) -> tuple[float, float]:
        values = self._curve_training_values(model, variable, stage_name, stage_position_m)
        if not values:
            current = self._curve_variable_current(candidate, variable, stage_name, stage_position_m)
            values = [current - max(abs(current) * 0.08, 1.0), current + max(abs(current) * 0.08, 1.0)]
        low, high = min(values), max(values)
        padding = max((high - low) * 0.08, 1e-6)
        lower_bound = 0.0 if variable.startswith(("composition.", "process.")) or variable.endswith(".time_min") else -273.15
        return max(lower_bound, low - padding), high + padding

    @classmethod
    def _set_curve_variable(
        cls,
        candidate: Candidate,
        variable: str,
        value: float,
        stage_name: str | None = None,
        stage_position_m: float | None = None,
    ) -> None:
        if variable.startswith("composition."):
            candidate.inputs.composition[variable.removeprefix("composition.")] = min(100.0, max(0.0, float(value)))
            return
        if variable.startswith("process."):
            if variable == "process.ls_mpm":
                if "ls_mpm" not in candidate.inputs.process:
                    raise ValueError(
                        "ライン速度はこの候補で未設定のため、応答曲線を作成できません"
                    )
                old_speed = float(candidate.inputs.process["ls_mpm"])
                new_speed = max(0.001, float(value))
                points = candidate.inputs.heat_pattern or []
                if candidate.inputs.heat_time_basis == "line_speed":
                    scaled_times = line_speed_scaled_times(points, old_speed, new_speed)
                    for point, time_s in zip(points, scaled_times):
                        point.time_s = time_s
                candidate.inputs.process["ls_mpm"] = new_speed
                return
            candidate.inputs.process[variable.removeprefix("process.")] = max(0.001, float(value))
            return
        if variable == "heat.peak_temperature_c":
            points = candidate.inputs.heat_pattern or []
            index = max(range(len(points)), key=lambda i: points[i].temperature_c)
            points[index].temperature_c = float(value)
            return
        if variable == HEAT_STAGE_TEMPERATURE_VARIABLE:
            points = candidate.inputs.heat_pattern or []
            if not points or not stage_name or stage_position_m is None:
                raise ValueError("工程温度の応答曲線には工程名と工程位置が必要です")
            normalized_name = stage_name.strip()
            matches = [point for point in points if (point.stage_name or "").strip() == normalized_name]
            if matches:
                current = sum(point.temperature_c for point in matches) / len(matches)
                delta = float(value) - current
                shifted = [point.temperature_c + delta for point in matches]
                if any(temperature < -273.15 or temperature > 1800 for temperature in shifted):
                    raise ValueError("工程温度の変更後に物理温度範囲を外れる点があります")
                for point, temperature in zip(matches, shifted):
                    point.temperature_c = temperature
                return
            line_speed = float(candidate.inputs.process.get("ls_mpm", 0.0))
            target_time = cls._heat_stage_position(points, line_speed, stage_position_m)
            coincident = next((point for point in points if math.isclose(point.time_s, target_time, abs_tol=1e-9)), None)
            if coincident is not None:
                coincident.temperature_c = float(value)
                return
            insert_at = next(index for index, point in enumerate(points) if point.time_s > target_time)
            if points[insert_at].segment_start:
                raise ValueError(f"工程位置 {stage_position_m:g} m はヒートパターンの非連続な工程境界内にあります")
            points.insert(insert_at, HeatPoint(
                time_s=target_time,
                temperature_c=float(value),
                stage_name=normalized_name,
                mapping_status="synthetic_response_curve",
            ))
            return
        if variable.startswith("heat."):
            parts = variable.split(".")
            index = int(parts[1])
            points = candidate.inputs.heat_pattern or []
            point = points[index]
            if parts[2] == "temperature_c":
                point.temperature_c = float(value)
            else:
                lower = points[index - 1].time_s + 1e-6 if index else 0.0
                upper = points[index + 1].time_s - 1e-6 if index + 1 < len(points) else float("inf")
                point.time_s = min(max(float(value) * 60, lower), upper)
            return
        raise ValueError(f"Unsupported response-curve variable: {variable}")

    def curve_variable(
        self,
        candidate: Candidate,
        variable: str,
        model: RidgeModel | None = None,
        axis_range: tuple[float, float] | None = None,
        stage_name: str | None = None,
        stage_position_m: float | None = None,
    ) -> dict[str, Any]:
        reference = model or next(iter(self.models.values()))
        labels = {"heat.peak_temperature_c": ("焼鈍履歴 最高温度", "°C")}
        if variable.startswith("composition."):
            label = variable.removeprefix("composition.")
            unit = "%"
        elif variable.startswith("process."):
            field_path = variable
            field = next((field for group in load_task_definitions()[TASK_ID].input_groups for field in group.fields if field.path == field_path), None)
            label, unit = (field.label, field.unit or "") if field else (variable.removeprefix("process."), "")
        elif variable == HEAT_STAGE_TEMPERATURE_VARIABLE:
            label, unit = (f"{stage_name} 温度", "°C")
        elif variable.startswith("heat.") and variable.count(".") == 2:
            _, raw_index, field = variable.split(".")
            label = f"ヒートパターン {int(raw_index) + 1}点目 { '時間' if field == 'time_min' else '温度' }"
            unit = "min" if field == "time_min" else "°C"
        else:
            label, unit = labels.get(variable, (variable, ""))
        training_values = self._curve_training_values(reference, variable, stage_name, stage_position_m)
        training_range = None if not training_values else {"min": round(min(training_values), 4), "max": round(max(training_values), 4)}
        axis_min, axis_max = axis_range or self._curve_axis(candidate, reference, variable, stage_name, stage_position_m)
        variable_id = f"{variable}:{stage_name}" if variable == HEAT_STAGE_TEMPERATURE_VARIABLE else variable
        return {"id": variable_id, "label": label, "unit": unit, "min": round(axis_min, 4), "max": round(axis_max, 4), "current": round(self._curve_variable_current(candidate, variable, stage_name, stage_position_m), 4), "training_range": training_range}

    def response_curve(
        self,
        candidate: Candidate,
        target: str = "TS",
        variable: str = "heat.peak_temperature_c",
        points: int = 9,
        axis_range: tuple[float, float] | None = None,
        stage_name: str | None = None,
        stage_position_m: float | None = None,
    ) -> list[dict[str, float]]:
        if target not in self.models:
            return []
        model = self.models[target]
        start, end = axis_range or self._curve_axis(candidate, model, variable, stage_name, stage_position_m)
        lower_offset, upper_offset = model.interval_offsets()
        curve: list[dict[str, float]] = []
        current = self._curve_variable_current(candidate, variable, stage_name, stage_position_m)
        for x_value in anchored_curve_grid(start, end, points, current=current):
            adjusted = candidate.model_copy(deep=True)
            self._set_curve_variable(adjusted, variable, float(x_value), stage_name, stage_position_m)
            adjusted_vector = self.vector_for_candidate(adjusted)
            summary = None
            if target in self.package_predictors:
                summary = self.package_predictors[target].predict({name: float(value) for name, value in zip(self.feature_names, adjusted_vector)}, seed=0)
                value = summary.point_estimate
                lower, upper = predictive_interval(summary)
            else:
                value = model.predict(adjusted_vector)
                lower, upper = value + lower_offset, value + upper_offset
            curve.append({
                "x": round(float(x_value), 4),
                "value": round(value, 3),
                "lower": round(lower, 3),
                "upper": round(upper, 3),
                "target_kind": summary.target_kind if summary is not None else "continuous",
                "point_statistic": summary.point_statistic if summary is not None else "mean",
                "predictive_family": summary.distribution.get("family", "empirical_quantiles") if summary is not None else "empirical_quantiles",
                "quantiles": {"0.05": round(lower, 6), "0.95": round(upper, 6)} if summary is None else {level: round(float(item), 6) for level, item in summary.quantiles.items()},
                "categories": [] if summary is None else list(summary.distribution.get("categories", [])),
            })
        return curve

    def response_curve_result(
        self,
        candidate: Candidate,
        target: str,
        variable: str,
        points: int,
        axis_range: tuple[float, float] | None = None,
        stage_name: str | None = None,
        stage_position_m: float | None = None,
    ) -> dict[str, Any]:
        if target not in self.output_keys:
            raise ValueError(f"Unsupported response-curve target: {target}")
        model = self.models.get(target)
        if model is None:
            raise ValueError(f"Response curve has no calibrated reference model for: {target}")
        column, _ = TARGETS[target]
        observed = [float(row["outputs"][column]) for row in model.rows if column in row["outputs"]]
        output_range = None if not observed else {"min": round(min(observed), 4), "max": round(max(observed), 4)}
        return {
            "target": target,
            "variable": self.curve_variable(candidate, variable, model, axis_range, stage_name, stage_position_m),
            "points": self.response_curve(candidate, target, variable, points, axis_range, stage_name, stage_position_m),
            "output_range": output_range,
            "point_count": points,
            "policy_id": "anchored-grid-v1",
        }
from material_workbench.modeling.curve_grid import anchored_curve_grid
