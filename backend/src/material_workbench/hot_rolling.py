from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Any

import numpy as np

from .hot_rolling_feature_pipeline import FEATURE_COMPONENTS, FEATURE_NAMES, INPUT_SCHEMA_VERSION, PIPELINE_ID, PIPELINE_VERSION, build_hot_rolling_features
from .dataset_profile import load_task_definitions
from .importer import WorkbookData
from .model_packages import ModelPackageLoader, validate_task_definition_canonical_inputs
from .schemas import HotRollingCandidate, HotRollingCandidateInput, Prediction, Support


TASK_ID = "hot-rolled-properties-v1"


def _distance(reference: np.ndarray, point: np.ndarray, columns: tuple[int, ...] | None = None) -> np.ndarray:
    if columns is not None:
        return np.sqrt(((reference[:, columns] - point[list(columns)]) ** 2).mean(axis=1))
    parts = [((reference[:, columns] - point[list(columns)]) ** 2).mean(axis=1) for columns in FEATURE_COMPONENTS.values()]
    return np.sqrt(np.vstack(parts).mean(axis=0))


class HotRollingRuntime:
    task_id = TASK_ID

    def __init__(self, data: WorkbookData, package_root: str | Path | None = None) -> None:
        self.data = data
        default = Path(__file__).resolve().parents[3] / "models" / "packages" / "hot-rolled-gp-2026-07"
        self.model_package = ModelPackageLoader().load(package_root or default)
        manifest = self.model_package.manifest
        validate_task_definition_canonical_inputs(load_task_definitions()[TASK_ID], manifest)
        if manifest.task_id != TASK_ID:
            raise ValueError(f"Model package task {manifest.task_id} is incompatible with {TASK_ID}")
        if (manifest.feature_pipeline.id, manifest.feature_pipeline.version) != (PIPELINE_ID, PIPELINE_VERSION):
            raise ValueError("Hot-rolling model package feature pipeline is incompatible")
        if tuple(manifest.feature_pipeline.output_features) != FEATURE_NAMES:
            raise ValueError("Hot-rolling model package feature order is incompatible")
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
        candidate = HotRollingCandidateInput.model_validate(json.loads(self.model_package.artifact_path(smoke["input"]).read_text(encoding="utf-8")))
        expected = json.loads(self.model_package.artifact_path(smoke["expected"]).read_text(encoding="utf-8"))
        values = build_hot_rolling_features(candidate, self.composition_defaults).as_dict()
        actual = {target: predictor.predict(values).point_estimate for target, predictor in self.predictors.items()}
        if set(actual) != set(expected) or any(not np.isclose(actual[target], expected[target], rtol=1e-7, atol=1e-7) for target in actual):
            raise ValueError("Hot-rolling model package smoke test did not reproduce expected predictions")

    def _candidate_for_observation(self, row: dict[str, Any]) -> HotRollingCandidateInput:
        process = row["features"]
        return HotRollingCandidateInput(
            name=str(row["parent_key"]),
            composition=row["composition"],
            reheat_temperature_c=process["reheat_temperature_c"],
            hold_time_min=process["hold_time_min"],
            finish_temperature_c=process["finish_temperature_c"],
            coiling_temperature_c=process["coiling_temperature_c"],
            cooling_rate_c_s=process["cooling_rate_c_s"],
            entry_thickness_mm=process["entry_thickness_mm"],
            exit_thickness_mm=process["exit_thickness_mm"],
            route=process["route"],
        )

    def _build_support_reference(self) -> None:
        observations = [row for row in self.data.observations if row["source"] == "熱延引張" and row["eligible"] and row["features"] and row["composition"]]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in observations:
            grouped[str(row["parent_key"])].append(row)
        self.reference_rows = [rows for _, rows in sorted(grouped.items())]
        raw = np.vstack([
            build_hot_rolling_features(self._candidate_for_observation(rows[0]), self.composition_defaults).values
            for rows in self.reference_rows
        ])
        self.reference_mean = raw.mean(axis=0)
        self.reference_scale = raw.std(axis=0)
        self.reference_scale[self.reference_scale < 1e-9] = 1.0
        self.reference_vectors = (raw - self.reference_mean) / self.reference_scale
        pairwise = np.sqrt(((self.reference_vectors[:, None, :] - self.reference_vectors[None, :, :]) ** 2).mean(axis=2))
        np.fill_diagonal(pairwise, np.inf)
        self.loo_nearest = pairwise.min(axis=1)

    def vector(self, candidate: HotRollingCandidateInput) -> np.ndarray:
        return build_hot_rolling_features(candidate, self.composition_defaults).values

    def _support(self, candidate: HotRollingCandidateInput) -> tuple[Support, list[dict[str, Any]]]:
        normalized = (self.vector(candidate) - self.reference_mean) / self.reference_scale
        distances = _distance(self.reference_vectors, normalized)
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
        for index in np.argsort(distances)[:3]:
            repeats = self.reference_rows[int(index)]
            values: dict[str, list[float]] = defaultdict(list)
            for repeat in repeats:
                for name, value in repeat["outputs"].items():
                    values[name].append(float(value))
            nearest_rows.append({
                "parent_key": repeats[0]["parent_key"],
                "test_direction": "L",
                "distance": round(float(distances[index]), 4),
                "components": {
                    name: round(float(_distance(self.reference_vectors, normalized, columns)[int(index)]), 4)
                    for name, columns in FEATURE_COMPONENTS.items()
                },
                "repeat_summary": {
                    name: {"mean": round(float(np.mean(items)), 3), "std": round(float(np.std(items)), 3), "n": len(items)}
                    for name, items in sorted(values.items())
                },
            })
        return Support(
            status=status,
            distance=round(nearest, 4),
            percentile=round(float((self.loo_nearest <= nearest).mean() * 100), 1),
            message=message,
            components={name: round(float(_distance(self.reference_vectors, normalized, columns)[nearest_index]), 4) for name, columns in FEATURE_COMPONENTS.items()},
            reference_count=len(self.reference_rows),
            supported_threshold=round(supported_limit, 4),
            caution_threshold=round(caution_limit, 4),
        ), nearest_rows

    @property
    def output_keys(self) -> frozenset[str]:
        return frozenset(self.predictors)

    def predict(self, candidate: HotRollingCandidate, detailed: bool = False, **_: Any) -> dict[str, Any]:
        bundle = build_hot_rolling_features(candidate, self.composition_defaults)
        values = bundle.as_dict()
        support, similar = self._support(candidate)
        predictions: dict[str, Prediction] = {}
        for target, predictor in self.predictors.items():
            summary = predictor.predict(values)
            predictions[target] = Prediction(
                value=round(summary.point_estimate, 3),
                lower=round(summary.quantiles.get("0.05", summary.point_estimate), 3),
                upper=round(summary.quantiles.get("0.95", summary.point_estimate), 3),
                unit=summary.unit,
                uncertainty_components=None if summary.uncertainty_components is None else {
                    name: round(float(value), 6) for name, value in summary.uncertainty_components.items()
                },
            )
        process = candidate.model_dump(exclude={"name", "composition"})
        process["reduction_percent"] = round((1.0 - candidate.exit_thickness_mm / candidate.entry_thickness_mm) * 100.0, 4)
        process["equipment"] = "HR-LINE-1"
        process["test_direction"] = "L"
        return {
            "task_id": self.task_id,
            "candidate_id": candidate.id,
            "mode": "detailed" if detailed else "preview",
            "predictions": predictions,
            "support": support,
            "warnings": [] if support.status == "supported" else [support.message],
            "similar": similar,
            "canonical_input": {
                "input_schema_version": INPUT_SCHEMA_VERSION,
                "composition_mass_percent": {name: values[name] for name in self.composition_defaults},
                "process": process,
                "feature_vector": values,
            },
            "model_meta": {
                "model": {"id": self.model_package.manifest.package_id, "version": self.model_package.manifest.package_version, "method": "Gaussian process regression"},
                "feature_pipeline": {"id": PIPELINE_ID, "version": PIPELINE_VERSION},
                "training_data": {"records": self.training_counts},
                "prediction_interval": {"method": "gaussian_process_predictive_distribution", "coverage": "central 90% predictive interval"},
            },
            "heat_pattern": [],
            "response_curve": None,
        }
