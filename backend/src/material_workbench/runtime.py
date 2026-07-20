from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .feature_contracts import feature_index_families
from .feature_pipeline import FEATURE_DEFINITIONS, FEATURE_NAMES as METALLURGY_FEATURE_NAMES, FEATURE_PIPELINE_ID, FEATURE_PIPELINE_VERSION, build_feature_bundle, build_feature_bundle_from_observation
from .dataset_profile import load_task_definitions
from .importer import WorkbookData, composition_names
from .model_packages import ModelPackageLoader, VerifiedModelPackage, validate_predictive_summary, validate_task_definition_canonical_inputs
from .schemas import Candidate, CandidateInput, Prediction, Support
from .task_registry import load_task_contracts


TASK_ID = "annealed-properties-v1"
COMPOSITION_COLUMNS = composition_names(task_id=TASK_ID)
MODEL_ID = "anneal-ridge"
MODEL_VERSION = "0.4.0-oof-v1"
SIMILARITY_VERSION = "parent-condition-knn-v3-repeat-summary"
INPUT_SCHEMA_VERSION = "candidate-v1"
TARGETS = {"TS": ("TS[MPa]", "MPa"), "YS": ("YS[MPa]", "MPa"), "EL": ("EL[%]", "%"), "lambda": ("λ[%]", "%")}
FEATURE_NAMES = METALLURGY_FEATURE_NAMES
FEATURE_GROUP_INDICES = feature_index_families(
    FEATURE_DEFINITIONS,
    {
        "composition": ("composition",),
        "process": ("process", "categorical"),
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


def _rms_distance(reference: np.ndarray, point: np.ndarray, columns: tuple[int, ...] | None = None) -> np.ndarray:
    if columns is not None:
        return np.sqrt(((reference[:, columns] - point[list(columns)]) ** 2).mean(axis=1))
    # Give composition, process, metallurgy and heat-history evidence equal
    # influence regardless of how many scalar features each group contains.
    component_mse = [
        ((reference[:, group] - point[list(group)]) ** 2).mean(axis=1)
        for group in FEATURE_GROUP_INDICES.values()
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

    def __init__(self, data: WorkbookData, package_root: str | Path | None = None, *, load_package: bool = True) -> None:
        self.data = data
        default_package = Path(__file__).resolve().parents[3] / "models" / "packages" / "annealed-gp-2026-07"
        self.model_package: VerifiedModelPackage | None = (
            ModelPackageLoader().load(package_root or default_package)
            if load_package and (package_root or default_package.exists())
            else None
        )
        self.composition_defaults = dict(data.medians)
        self.composition_defaults_sha256: str | None = None
        if self.model_package:
            self._load_and_validate_package_feature_contract()
        self.models: dict[str, RidgeModel] = {}
        self.support_reference: SupportReference | None = None
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
        expected = tuple(FEATURE_NAMES)
        if (manifest.feature_pipeline.id, manifest.feature_pipeline.version) != (FEATURE_PIPELINE_ID, FEATURE_PIPELINE_VERSION):
            raise ValueError("Model package feature pipeline id/version is incompatible")
        if tuple(manifest.feature_pipeline.output_features) != expected:
            raise ValueError("Model package feature order does not match the application pipeline")
        if any(tuple(predictor.feature_names) != expected for predictor in manifest.predictors):
            raise ValueError("Model package predictor feature order is inconsistent")
        pipeline = json.loads(self.model_package.artifact_path(manifest.feature_pipeline.spec).read_text(encoding="utf-8"))
        if (pipeline.get("id"), pipeline.get("version")) != (FEATURE_PIPELINE_ID, FEATURE_PIPELINE_VERSION):
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
        candidate = CandidateInput.model_validate(json.loads(self.model_package.artifact_path(smoke["input"]).read_text(encoding="utf-8")))
        expected = json.loads(self.model_package.artifact_path(smoke["expected"]).read_text(encoding="utf-8"))
        values = build_feature_bundle(candidate, self.composition_defaults).as_dict()
        specs = {spec.target: spec for spec in self.model_package.manifest.predictors}
        capabilities = {item.target: item for item in load_task_contracts()[TASK_ID].runtime_capability.targets}
        summaries = {target: predictor.predict(values, seed=0) for target, predictor in self.package_predictors.items()}
        for target, summary in summaries.items():
            validate_predictive_summary(summary, specs[target], capabilities[target])
        actual = {target: summary.point_estimate for target, summary in summaries.items()}
        if set(actual) != set(expected) or any(not np.isclose(actual[target], expected[target], rtol=1e-7, atol=1e-7) for target in actual):
            raise ValueError("Model package smoke test did not reproduce its expected predictions")

    def vector_for_candidate(self, candidate: Candidate) -> np.ndarray:
        return build_feature_bundle(candidate, self.composition_defaults).values.copy()

    def canonical_input(self, candidate: Candidate) -> dict[str, Any]:
        bundle = build_feature_bundle(candidate, self.composition_defaults)
        vector = bundle.values.copy()
        feature_values = bundle.as_dict()
        normalized_vectors = {
            target: {name: round(float(value), 10) for name, value in zip(FEATURE_NAMES, (vector - model.feature_mean) / model.feature_scale)}
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
            "feature_vector": {name: round(float(value), 10) for name, value in zip(FEATURE_NAMES, vector)},
            "normalized_feature_vectors": normalized_vectors,
        }

    def _vector_for_observation(self, row: dict[str, Any]) -> np.ndarray | None:
        bundle = build_feature_bundle_from_observation(row, self.data.medians)
        return None if bundle is None else bundle.values.copy()

    @staticmethod
    def _support_reference(rows: list[dict[str, Any]], x: np.ndarray, mean: np.ndarray | None = None, scale: np.ndarray | None = None) -> SupportReference:
        mean = x.mean(axis=0) if mean is None else mean
        scale = x.std(axis=0) if scale is None else scale.copy()
        scale[scale < 1e-9] = 1.0
        normalized = (x - mean) / scale
        grouped_indexes: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(rows):
            grouped_indexes[str(row["parent_key"])].append(index)
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
        prepared = [(row, vector) for row, vector in prepared_all if row["eligible"] and row["source"] != "熱延引張"]
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
            groups = [str(row["parent_key"]) for row, _, _ in rows]
            oof_residuals, folds = _grouped_oof_residuals(x, y, groups)
            mean, scale, weights = _fit_ridge(x, y)
            normalized = (x - mean) / scale
            self.models[label] = RidgeModel(label, unit, mean, scale, weights, oof_residuals, normalized, [row for row, _, _ in rows], folds)

    def _support(self, x: np.ndarray) -> tuple[Support, list[dict[str, Any]]]:
        reference = self.support_reference
        if reference is None:
            raise RuntimeError("No eligible observations are available for support estimation")
        normalized = reference.normalized(x)
        distances = _rms_distance(reference.parent_vectors, normalized)
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
            for name, columns in FEATURE_GROUP_INDICES.items()
        }
        def nearest_rows(source: SupportReference, layer: str, limit: int, exclude: set[str] | None = None) -> list[dict[str, Any]]:
            source_normalized = source.normalized(x)
            source_distances = _rms_distance(source.parent_vectors, source_normalized)
            rows: list[dict[str, Any]] = []
            for index in np.argsort(source_distances):
                row = source.parent_rows[int(index)]
                parent_key = str(row["parent_key"])
                if exclude and parent_key in exclude:
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
                        for name, columns in FEATURE_GROUP_INDICES.items()
                    },
                    "outputs": {key: summary["mean"] for key, summary in summaries.items()},
                    "repeat_summary": summaries,
                })
                if len(rows) >= limit:
                    break
            return rows

        training_nearest = nearest_rows(reference, "training", 3)
        training_parents = {str(row["parent_key"]) for row in reference.parent_rows}
        historical_nearest = nearest_rows(self.historical_reference, "historical", 3, training_parents) if self.historical_reference else []
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
            "feature_pipeline": {"id": FEATURE_PIPELINE_ID, "version": FEATURE_PIPELINE_VERSION, "input_schema_version": INPUT_SCHEMA_VERSION, "features": list(FEATURE_NAMES)},
            "training_data": {"source_path": self.data.source_path, "source_sha256": self.data.source_sha256, "source_mtime_ns": self.data.source_mtime_ns, "records": {name: len(model.rows) for name, model in self.models.items()}},
            "prediction_interval": {
                "method": "grouped_oof_residual_quantiles",
                "coverage": "empirical central 90% residual interval",
                "grouping": "parent_key",
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
            if any(item.runtime_type in {"builtin.exact_gp.v1", "builtin.multitask_gp.v1", "gpytorch.static_exact_rbf.v1"} for item in manifest.predictors):
                meta["prediction_interval"] = {
                    "method": "gaussian_process_predictive_distribution",
                    "coverage": "central 90% predictive interval",
                    "grouping": "parent_key",
                    "note": "Model uncertainty and observation noise are reported separately; data support remains an independent distance-based check.",
                }
        return meta

    def predict(self, candidate: Candidate, detailed: bool = False, include_curve: bool = False, target_values: dict[str, float] | None = None) -> dict[str, Any]:
        x = self.vector_for_candidate(candidate)
        support, similar = self._support(x)
        predictions: dict[str, Prediction] = {}
        warnings: list[str] = []
        labels = self.package_predictors.keys() if self.package_predictors else self.models.keys()
        for label in labels:
            model = self.models.get(label)
            summary = None
            if label in self.package_predictors:
                summary = self.package_predictors[label].predict({name: float(value) for name, value in zip(FEATURE_NAMES, x)}, seed=0)
                value = summary.point_estimate
                lower = summary.quantiles.get("0.05", value)
                upper = summary.quantiles.get("0.95", value)
                unit = summary.unit
                warnings.extend(summary.warnings)
            else:
                assert model is not None
                value = model.predict(x)
                lower_offset, upper_offset = model.interval_offsets()
                lower, upper = value + lower_offset, value + upper_offset
                unit = model.unit
            goal_value = (target_values or {}).get(label)
            goal_probability = None
            if goal_value is not None and summary is not None and summary.event_probability is not None:
                goal_probability = summary.event_probability
            elif (
                goal_value is not None
                and summary is not None
                and summary.distribution.get("family") == "normal"
                and float(summary.distribution.get("std", 0.0)) > 0
            ):
                standard_deviation = float(summary.distribution["std"])
                goal_probability = 0.5 * math.erfc((goal_value - value) / (standard_deviation * math.sqrt(2.0)))
            elif goal_value is not None and summary is not None and model is not None:
                spec = self.package_predictor_specs[label]
                package_data_id = self.model_package.manifest.provenance.training_data_id if self.model_package else ""
                calibrated_same_data = (
                    spec.runtime_type == "builtin.linear.v1"
                    and spec.config.get("calibration") == "grouped_oof_residual_quantiles"
                    and package_data_id == f"sha256:{self.data.source_sha256}"
                )
                if calibrated_same_data:
                    goal_probability = float(np.mean(value + model.oof_residuals >= goal_value))
            elif goal_value is not None and model is not None:
                goal_probability = float(np.mean(value + model.oof_residuals >= goal_value))
            predictions[label] = Prediction(
                value=round(value, 3), lower=round(lower, 3), upper=round(upper, 3), unit=unit,
                goal_value=goal_value,
                goal_probability=None if goal_probability is None else round(goal_probability, 4),
                goal_direction=None if goal_value is None else "at_least",
                uncertainty_components=None if summary is None or summary.uncertainty_components is None else {
                    name: round(float(component), 6)
                    for name, component in summary.uncertainty_components.items()
                },
            )
        if support.status != "supported":
            warnings.append(support.message)
        if candidate.inputs.composition.get("C", self.data.medians["C"]) > 1:
            warnings.append("C量が参照データの通常域から大きく外れています")
        response_curve = self.response_curve(candidate) if include_curve else None
        return {
            "task_id": self.task_id, "candidate_id": candidate.id, "mode": "detailed" if detailed else "preview", "predictions": predictions,
            "support": support, "warnings": warnings, "model_meta": self._model_meta(),
            "canonical_input": self.canonical_input(candidate), "similar": similar,
            "heat_pattern": candidate.inputs.heat_pattern or [], "response_curve": response_curve,
        }

    def _curve_variable_current(self, candidate: Candidate, variable: str) -> float:
        if variable.startswith("composition."):
            field = variable.removeprefix("composition.")
            if field not in COMPOSITION_COLUMNS:
                raise ValueError(f"Unsupported response-curve variable: {variable}")
            return float(candidate.inputs.composition.get(field, self.composition_defaults[field]))
        if variable == "thickness_mm":
            return float(candidate.inputs.process["thickness_mm"])
        if variable == "line_speed_m_min":
            return float(candidate.inputs.process["line_speed_m_min"])
        if variable == "heat.peak_temperature_c":
            return float(max(point.temperature_c for point in (candidate.inputs.heat_pattern or [])))
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

    def _curve_training_values(self, model: RidgeModel, variable: str) -> list[float]:
        values: list[float] = []
        for row in model.rows:
            try:
                if variable.startswith("composition."):
                    value = row["composition"].get(variable.removeprefix("composition."))
                elif variable == "thickness_mm":
                    value = row["thickness_mm"]
                elif variable == "line_speed_m_min":
                    value = row["features"].get("line_speed_m_min")
                elif variable == "heat.peak_temperature_c":
                    value = max(point["temperature_c"] for point in row["features"].get("heat_pattern", []))
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

    def _curve_axis(self, candidate: Candidate, model: RidgeModel, variable: str) -> tuple[float, float]:
        values = self._curve_training_values(model, variable)
        if not values:
            current = self._curve_variable_current(candidate, variable)
            values = [current - max(abs(current) * 0.08, 1.0), current + max(abs(current) * 0.08, 1.0)]
        low, high = min(values), max(values)
        padding = max((high - low) * 0.08, 1e-6)
        lower_bound = 0.0 if variable.startswith("composition.") or variable in {"thickness_mm", "line_speed_m_min"} or variable.endswith(".time_min") else -273.15
        return max(lower_bound, low - padding), high + padding

    @staticmethod
    def _set_curve_variable(candidate: Candidate, variable: str, value: float) -> None:
        if variable.startswith("composition."):
            candidate.inputs.composition[variable.removeprefix("composition.")] = min(100.0, max(0.0, float(value)))
            return
        if variable == "thickness_mm":
            candidate.inputs.process["thickness_mm"] = max(0.001, float(value))
            return
        if variable == "line_speed_m_min":
            candidate.inputs.process["line_speed_m_min"] = max(0.001, float(value))
            return
        if variable == "heat.peak_temperature_c":
            points = candidate.inputs.heat_pattern or []
            index = max(range(len(points)), key=lambda i: points[i].temperature_c)
            points[index].temperature_c = float(value)
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

    def curve_variable(self, candidate: Candidate, variable: str, model: RidgeModel | None = None) -> dict[str, Any]:
        reference = model or next(iter(self.models.values()))
        labels = {
            "thickness_mm": ("板厚", "mm"),
            "line_speed_m_min": ("ライン速度", "m/min"),
            "heat.peak_temperature_c": ("ヒートパターン 最高温度", "°C"),
        }
        if variable.startswith("composition."):
            label = variable.removeprefix("composition.")
            unit = "mass%"
        elif variable.startswith("heat.") and variable.count(".") == 2:
            _, raw_index, field = variable.split(".")
            label = f"ヒートパターン {int(raw_index) + 1}点目 { '時間' if field == 'time_min' else '温度' }"
            unit = "min" if field == "time_min" else "°C"
        else:
            label, unit = labels.get(variable, (variable, ""))
        axis_min, axis_max = self._curve_axis(candidate, reference, variable)
        return {"id": variable, "label": label, "unit": unit, "min": round(axis_min, 4), "max": round(axis_max, 4), "current": round(self._curve_variable_current(candidate, variable), 4)}

    def response_curve(self, candidate: Candidate, target: str = "TS", variable: str = "heat.peak_temperature_c") -> list[dict[str, float]]:
        if target not in self.models:
            return []
        model = self.models[target]
        start, end = self._curve_axis(candidate, model, variable)
        lower_offset, upper_offset = model.interval_offsets()
        curve: list[dict[str, float]] = []
        for x_value in np.linspace(start, end, 9):
            adjusted = candidate.model_copy(deep=True)
            self._set_curve_variable(adjusted, variable, float(x_value))
            adjusted_vector = self.vector_for_candidate(adjusted)
            if target in self.package_predictors:
                summary = self.package_predictors[target].predict({name: float(value) for name, value in zip(FEATURE_NAMES, adjusted_vector)}, seed=0)
                value = summary.point_estimate
                lower, upper = summary.quantiles.get("0.05", value), summary.quantiles.get("0.95", value)
            else:
                value = model.predict(adjusted_vector)
                lower, upper = value + lower_offset, value + upper_offset
            curve.append({"x": round(float(x_value), 4), "value": round(value, 3), "lower": round(lower, 3), "upper": round(upper, 3)})
        return curve

    def response_curves(self, candidate: Candidate, variable: str | None = None) -> dict[str, Any]:
        """Return response curves and axis metadata for one selected design variable."""
        selected = variable or "heat.peak_temperature_c"
        curves = {target: self.response_curve(candidate, target, selected) for target in TARGETS}
        if variable is None:
            return curves
        model = next(iter(self.models.values()), None)
        if model is None:
            return {"variable": {"id": selected, "label": selected, "unit": "", "min": 0, "max": 1, "current": 0}, "curves": curves, "output_ranges": {}}
        output_ranges: dict[str, dict[str, float]] = {}
        for target, (column, _) in TARGETS.items():
            target_model = self.models.get(target)
            values = [float(row["outputs"][column]) for row in target_model.rows if column in row["outputs"]] if target_model else []
            if values:
                output_ranges[target] = {"min": round(min(values), 4), "max": round(max(values), 4)}
        return {"variable": self.curve_variable(candidate, selected, model), "curves": curves, "output_ranges": output_ranges}
