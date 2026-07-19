from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np

from .importer import COMPOSITION_COLUMNS, WorkbookData
from .schemas import Candidate, Prediction, Support


TASK_ID = "annealed-properties-v1"
MODEL_ID = "anneal-ridge"
MODEL_VERSION = "0.1.0-oof-v1"
FEATURE_PIPELINE_ID = "anneal-heat-summary"
FEATURE_PIPELINE_VERSION = "1.0.0"
SIMILARITY_VERSION = "parent-condition-knn-v1"
INPUT_SCHEMA_VERSION = "candidate-v1"
TARGETS = {"TS": ("TS[MPa]", "MPa"), "YS": ("YS[MPa]", "MPa"), "EL": ("EL[%]", "%"), "lambda": ("λ[%]", "%")}
FEATURE_NAMES = tuple(COMPOSITION_COLUMNS) + ("thickness_mm", "line_speed_m_min", "max_temperature_c", "hold_time_s", "coating_GI", "coating_GA", "reheat")
FEATURE_COMPONENTS = {
    "composition": tuple(range(len(COMPOSITION_COLUMNS))),
    "process": (len(COMPOSITION_COLUMNS), len(COMPOSITION_COLUMNS) + 1, len(COMPOSITION_COLUMNS) + 4, len(COMPOSITION_COLUMNS) + 5),
    "heat_pattern": (len(COMPOSITION_COLUMNS) + 2, len(COMPOSITION_COLUMNS) + 3, len(COMPOSITION_COLUMNS) + 6),
}


def _heat_metrics(pattern: list[Any]) -> tuple[float, float, float]:
    temperatures = [float(point.temperature_c) for point in pattern]
    times = [float(point.time_s) for point in pattern]
    peak = max(temperatures)
    peak_index = temperatures.index(peak)
    # Time between entering and leaving the 95% peak band is a defensible hold proxy for free-form input.
    band = [time for time, temp in zip(times, temperatures) if temp >= peak * 0.95]
    hold = max(band) - min(band) if len(band) > 1 else 0.0
    reheat = 1.0 if any(temperatures[i] < temperatures[i + 1] - 25 for i in range(peak_index, len(temperatures) - 1)) else 0.0
    return peak, hold, reheat


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
    selected_reference = reference if columns is None else reference[:, columns]
    selected_point = point if columns is None else point[list(columns)]
    return np.sqrt(((selected_reference - selected_point) ** 2).mean(axis=1))


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
    loo_nearest_distances: np.ndarray

    def normalized(self, x: np.ndarray) -> np.ndarray:
        return (x - self.feature_mean) / self.feature_scale


class ModelRuntime:
    def __init__(self, data: WorkbookData) -> None:
        self.data = data
        self.models: dict[str, RidgeModel] = {}
        self.support_reference: SupportReference | None = None
        self._fit()

    def vector_for_candidate(self, candidate: Candidate) -> np.ndarray:
        peak, hold, reheat = _heat_metrics(candidate.heat_pattern)
        composition = {name: candidate.composition.get(name, self.data.medians[name]) for name in COMPOSITION_COLUMNS}
        return np.array([*(float(composition[name]) for name in COMPOSITION_COLUMNS), candidate.thickness_mm, candidate.line_speed_m_min, peak, hold, 1.0 if candidate.coating == "GI" else 0.0, 1.0 if candidate.coating == "GA" else 0.0, reheat], dtype=float)

    def canonical_input(self, candidate: Candidate) -> dict[str, Any]:
        vector = self.vector_for_candidate(candidate)
        peak, hold, reheat = _heat_metrics(candidate.heat_pattern)
        normalized_vectors = {
            target: {name: round(float(value), 10) for name, value in zip(FEATURE_NAMES, (vector - model.feature_mean) / model.feature_scale)}
            for target, model in self.models.items()
        }
        return {
            "input_schema_version": INPUT_SCHEMA_VERSION,
            "composition_mass_percent": {name: round(float(vector[index]), 8) for index, name in enumerate(COMPOSITION_COLUMNS)},
            "process": {
                "thickness_mm": candidate.thickness_mm,
                "line_speed_m_min": candidate.line_speed_m_min,
                "coating": candidate.coating,
            },
            "heat_pattern": [point.model_dump(mode="json") for point in candidate.heat_pattern],
            "heat_summary": {"max_temperature_c": peak, "hold_time_s": hold, "reheat": bool(reheat)},
            "feature_vector": {name: round(float(value), 10) for name, value in zip(FEATURE_NAMES, vector)},
            "normalized_feature_vectors": normalized_vectors,
        }

    def _vector_for_observation(self, row: dict[str, Any]) -> np.ndarray | None:
        process, composition = row["features"], row["composition"]
        if not process or not composition:
            return None
        return np.array([*(float(composition.get(name, self.data.medians[name])) for name in COMPOSITION_COLUMNS), row["thickness_mm"], process["line_speed_m_min"], process["max_temperature_c"], process["hold_time_s"], 1.0 if process["coating"] == "GI" else 0.0, 1.0 if process["coating"] == "GA" else 0.0, process["reheat"]], dtype=float)

    @staticmethod
    def _support_reference(rows: list[dict[str, Any]], x: np.ndarray) -> SupportReference:
        mean = x.mean(axis=0)
        scale = x.std(axis=0)
        scale[scale < 1e-9] = 1.0
        normalized = (x - mean) / scale
        grouped_indexes: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(rows):
            grouped_indexes[str(row["parent_key"])].append(index)
        ordered_groups = sorted(grouped_indexes)
        parent_vectors = np.vstack([normalized[grouped_indexes[group]].mean(axis=0) for group in ordered_groups])
        parent_rows = [rows[grouped_indexes[group][0]] for group in ordered_groups]
        pairwise = np.sqrt(((parent_vectors[:, None, :] - parent_vectors[None, :, :]) ** 2).mean(axis=2))
        np.fill_diagonal(pairwise, np.inf)
        loo_nearest = pairwise.min(axis=1) if len(parent_vectors) > 1 else np.array([0.0])
        return SupportReference(mean, scale, parent_vectors, parent_rows, loo_nearest)

    def _fit(self) -> None:
        prepared = [(row, self._vector_for_observation(row)) for row in self.data.observations]
        prepared = [(row, vector) for row, vector in prepared if row["eligible"] and vector is not None and row["source"] != "熱延引張"]
        if prepared:
            self.support_reference = self._support_reference([row for row, _ in prepared], np.vstack([vector for _, vector in prepared]))
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
            for name, columns in FEATURE_COMPONENTS.items()
        }
        nearest_indexes = np.argsort(distances)[:5]
        similar = []
        for index in nearest_indexes:
            row = reference.parent_rows[int(index)]
            similar.append({"observation_id": row["id"], "source": row["source"], "parent_key": row["parent_key"], "distance": round(float(distances[index]), 4), "outputs": {key: round(value, 3) for key, value in row["outputs"].items()}})
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
        return {
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
            "similarity": {"version": SIMILARITY_VERSION, "method": "parent-condition leave-one-out nearest-neighbor distance across normalized composition, process, and heat-summary features"},
        }

    def predict(self, candidate: Candidate, detailed: bool = False, include_curve: bool = False) -> dict[str, Any]:
        x = self.vector_for_candidate(candidate)
        support, similar = self._support(x)
        predictions: dict[str, Prediction] = {}
        for label, model in self.models.items():
            value = model.predict(x)
            lower_offset, upper_offset = model.interval_offsets()
            predictions[label] = Prediction(value=round(value, 3), lower=round(value + lower_offset, 3), upper=round(value + upper_offset, 3), unit=model.unit)
        warnings: list[str] = []
        if support.status != "supported":
            warnings.append(support.message)
        if candidate.composition.get("C", self.data.medians["C"]) > 1:
            warnings.append("C量が参照データの通常域から大きく外れています")
        response_curve = self.response_curve(candidate) if include_curve else None
        return {
            "candidate_id": candidate.id, "mode": "detailed" if detailed else "preview", "predictions": predictions,
            "support": support, "warnings": warnings, "model_meta": self._model_meta(),
            "canonical_input": self.canonical_input(candidate), "similar": similar,
            "heat_pattern": candidate.heat_pattern, "response_curve": response_curve,
        }

    def response_curve(self, candidate: Candidate) -> list[dict[str, float]]:
        if "TS" not in self.models:
            return []
        model = self.models["TS"]
        values = model.x_train[:, FEATURE_NAMES.index("max_temperature_c")] * model.feature_scale[FEATURE_NAMES.index("max_temperature_c")] + model.feature_mean[FEATURE_NAMES.index("max_temperature_c")]
        start, end = float(np.quantile(values, 0.05)), float(np.quantile(values, 0.95))
        peak_index = max(range(len(candidate.heat_pattern)), key=lambda i: candidate.heat_pattern[i].temperature_c)
        lower_offset, upper_offset = model.interval_offsets()
        curve: list[dict[str, float]] = []
        for temperature in np.linspace(start, end, 9):
            adjusted = candidate.model_copy(deep=True)
            adjusted.heat_pattern[peak_index].temperature_c = float(temperature)
            value = model.predict(self.vector_for_candidate(adjusted))
            curve.append({"temperature_c": round(float(temperature), 2), "value": round(value, 3), "lower": round(value + lower_offset, 3), "upper": round(value + upper_offset, 3)})
        return curve
