from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .feature_contracts import feature_index_families
from .hot_rolling_feature_pipeline import FEATURE_DEFINITIONS, FEATURE_NAMES, INPUT_SCHEMA_VERSION, PIPELINE_ID, PIPELINE_VERSION, build_hot_rolling_features, build_hot_rolling_features_from_observation
from .dataset_profile import load_task_definitions
from .importer import WorkbookData, lineage_reference_keys
from .model_packages import ModelPackageLoader, predictive_interval, validate_predictive_summary, validate_task_definition_canonical_inputs
from .schemas import Candidate, CandidateInput, Prediction, Support
from .task_registry import load_task_contracts


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


def _distance(reference: np.ndarray, point: np.ndarray, columns: tuple[int, ...] | None = None) -> np.ndarray:
    if columns is not None:
        return np.sqrt(((reference[:, columns] - point[list(columns)]) ** 2).mean(axis=1))
    parts = [((reference[:, columns] - point[list(columns)]) ** 2).mean(axis=1) for columns in FEATURE_GROUP_INDICES.values()]
    return np.sqrt(np.vstack(parts).mean(axis=0))


class HotRollingRuntime:
    task_id = TASK_ID
    support_policy_id = SUPPORT_POLICY_ID

    def __init__(self, data: WorkbookData, package_root: str | Path | None = None) -> None:
        self.data = data
        default = Path(__file__).resolve().parents[3] / "models" / "packages" / "hot-rolled-horseshoe-2026-07"
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
        candidate = CandidateInput.model_validate(json.loads(self.model_package.artifact_path(smoke.input).read_text(encoding="utf-8")))
        expected = json.loads(self.model_package.artifact_path(smoke.expected).read_text(encoding="utf-8"))
        values = build_hot_rolling_features(candidate, self.composition_defaults).as_dict()
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
            grouped[str(row["parent_key"])].append(row)
        self.reference_rows = [rows for _, rows in sorted(grouped.items())]
        bundles = [build_hot_rolling_features_from_observation(rows[0], self.composition_defaults) for rows in self.reference_rows]
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
        return build_hot_rolling_features(candidate, self.composition_defaults).values

    def _support(self, candidate: CandidateInput, *, include_similarity: bool = True) -> tuple[Support, list[dict[str, Any]]]:
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
                    "test_direction": "L",
                    "distance": round(float(distances[index]), 4),
                    "components": {
                        name: round(float(_distance(self.reference_vectors, normalized, columns)[int(index)]), 4)
                        for name, columns in FEATURE_GROUP_INDICES.items()
                    },
                    "repeat_summary": {
                        name: {"mean": round(float(np.mean(items)), 3), "std": round(float(np.std(items)), 3), "n": len(items)}
                        for name, items in sorted(values.items())
                    },
                    **lineage_reference_keys(self.data, str(repeats[0]["parent_key"]), "hot_rolling"),
                })
        return Support(
            status=status,
            distance=round(nearest, 4),
            percentile=round(float((self.loo_nearest <= nearest).mean() * 100), 1),
            message=message,
            components={name: round(float(_distance(self.reference_vectors, normalized, columns)[nearest_index]), 4) for name, columns in FEATURE_GROUP_INDICES.items()},
            reference_count=len(self.reference_rows),
            supported_threshold=round(supported_limit, 4),
            caution_threshold=round(caution_limit, 4),
        ), nearest_rows

    @property
    def output_keys(self) -> frozenset[str]:
        return frozenset(self.predictors)

    def predict_core(self, candidate: Candidate, detailed: bool = False, target_values: dict[str, float] | None = None, **_: Any) -> dict[str, Any]:
        bundle = build_hot_rolling_features(candidate, self.composition_defaults)
        values = bundle.as_dict()
        predictions: dict[str, Prediction] = {}
        for target, predictor in self.predictors.items():
            summary = predictor.predict(values)
            lower, upper = predictive_interval(summary)
            goal_value = (target_values or {}).get(target)
            standard_deviation = float(summary.distribution.get("std", 0))
            goal_probability = None
            if goal_value is not None and standard_deviation > 0:
                goal_probability = 0.5 * math.erfc((goal_value - summary.point_estimate) / (standard_deviation * math.sqrt(2.0)))
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
                goal_probability=None if goal_probability is None else round(goal_probability, 4),
                goal_direction=None if goal_value is None else "at_least",
                uncertainty_components=None if summary.uncertainty_components is None else {
                    name: round(float(value), 6) for name, value in summary.uncertainty_components.items()
                },
            )
        process = {**candidate.inputs.process}
        process["equipment"] = "HR-LINE-1"
        process["test_direction"] = "L"
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
                "feature_vector": values,
            },
            "model_meta": {
                "task_id": self.task_id,
                "model": {"id": self.model_package.manifest.package_id, "version": self.model_package.manifest.package_version, "method": "Regularized Horseshoe sparse Bayesian regression"},
                "package": {"id": self.model_package.manifest.package_id, "version": self.model_package.manifest.package_version, "manifest_sha256": self.model_package.manifest_sha256, "runtime_types": sorted({item.runtime_type for item in self.model_package.manifest.predictors})},
                "feature_pipeline": {"id": PIPELINE_ID, "version": PIPELINE_VERSION, "input_schema_version": INPUT_SCHEMA_VERSION, "features": list(FEATURE_NAMES)},
                "training_data": {"source_path": self.data.source_path, "source_sha256": self.data.source_sha256, "records": self.training_counts, "package_training_data_id": self.model_package.manifest.provenance.training_data_id, "package_feature_dataset_id": self.model_package.manifest.provenance.feature_dataset_id},
                "prediction_interval": {"method": "posterior_predictive_moment_matched_normal", "coverage": "central 90% predictive interval", "grouping": "parent_key", "note": "Horseshoe posterior draws are summarized as a moment-matched Normal distribution for the shared decision UI."},
                "similarity": {"version": SUPPORT_POLICY_ID, "method": "parent-condition nearest-neighbor distance over composition, metallurgy, and process feature groups"},
            },
            "heat_pattern": [],
            "response_curve": None,
        }

    def evidence(self, candidate: Candidate) -> tuple[Support, list[dict[str, Any]]]:
        return self._support(candidate)

    def support_summary(self, candidate: Candidate) -> Support:
        return self._support(candidate, include_similarity=False)[0]

    def similarity(self, candidate: Candidate, limit: int = 3) -> list[dict[str, Any]]:
        return self.evidence(candidate)[1][:limit]

    def predict(self, candidate: Candidate, detailed: bool = False, **kwargs: Any) -> dict[str, Any]:
        result = self.predict_core(candidate, detailed=detailed, **kwargs)
        support, similar = self.evidence(candidate)
        result["support"] = support
        result["similar"] = similar
        if support.status != "supported":
            result["warnings"].append(support.message)
        return result
